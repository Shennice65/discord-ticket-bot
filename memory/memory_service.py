import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class MemoryService:
    def __init__(self, bot):
        self.bot = bot

    async def load_memory_cache(self, limit: int = 1000, min_confidence: float = 0.1):
        """Loads recent relevant memories from the DB."""
        if getattr(self.bot, "db", None) is None or getattr(self.bot.db, "db", None) is None:
            return []
            
        try:
            collection = self.bot.db.db.chat_memory
            # Fetch recent general memories
            cursor = collection.find({
                "confidence": {"$gte": min_confidence}
            }).sort("timestamp", -1).limit(limit)
            
            records = await cursor.to_list(length=limit)
            
            # Ensure important server-wide lore is always in cache
            lore_cursor = collection.find({
                "record_type": {"$in": ["server_lore", "community_term", "inside_joke", "nickname", "relationship", "event"]},
                "confidence": {"$gte": min_confidence}
            })
            lore_records = await lore_cursor.to_list(length=2000)
            
            seen_ids = {r.get("_id") for r in records}
            for r in lore_records:
                if r.get("_id") not in seen_ids:
                    records.append(r)
            
            return records
        except Exception as e:
            logger.error(f"Failed to load memory cache: {e}")
            return []

    async def save_memory(self, guild_id, channel_id, memory_data: dict):
        if getattr(self.bot, "db", None) is None or getattr(self.bot.db, "db", None) is None:
            return
            
        try:
            memory_data["guild_id"] = guild_id
            memory_data["channel_id"] = channel_id
            memory_data["timestamp"] = datetime.now(timezone.utc)
            await self.bot.db.db.chat_memory.insert_one(memory_data)
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
