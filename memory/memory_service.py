import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class MemoryService:
    def __init__(self, bot):
        self.bot = bot

    async def load_memory_cache(self, channel_id: int, limit: int = 250, min_confidence: float = 0.5):
        """Loads recent relevant memories from the DB."""
        if not getattr(self.bot, "db", None) or not getattr(self.bot.db, "db", None):
            return []
            
        try:
            collection = self.bot.db.db.chat_memory
            # Note: The old AI data is dropped, this will return empty until new extractor runs
            cursor = collection.find({
                "channel_id": channel_id,
                "confidence": {"$gte": min_confidence}
            }).sort("timestamp", -1).limit(limit)
            
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Failed to load memory cache: {e}")
            return []

    async def save_memory(self, guild_id, channel_id, memory_data: dict):
        if not getattr(self.bot, "db", None) or not getattr(self.bot.db, "db", None):
            return
            
        try:
            memory_data["guild_id"] = guild_id
            memory_data["channel_id"] = channel_id
            memory_data["timestamp"] = datetime.now(timezone.utc)
            await self.bot.db.db.chat_memory.insert_one(memory_data)
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
