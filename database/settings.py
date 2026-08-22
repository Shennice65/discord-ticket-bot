import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
import asyncio


class SettingsMixin:
    async def _next_id(self, collection_name: str) -> int:
        """Atomically generate a unique auto-incrementing ID for a collection.
        Uses a counter stored in bot_settings so IDs never collide even after deletions."""
        result = await self.bot_settings.find_one_and_update(
            {"key": f"counter_{collection_name}"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=True
        )
        return result["value"]
            
    async def get_setting(self, key: str, default=None):
        """Get a setting from the database."""
        setting = await self.bot_settings.find_one({"key": key})
        return setting.get("value") if setting else default
        
    async def set_setting(self, key: str, value):
        """Set a setting in the database."""
        await self.bot_settings.update_one(
            {"key": key},
            {"$set": {"value": value}},
            upsert=True
        )
            
    async def log_undo_action(self, target_id: int, action_type: str, old_rank: str, new_rank: str, observer_id: Optional[int] = None, old_streak: Optional[int] = None, source: Optional[str] = None):
        """Log an action so it can be undone later."""
        doc = {
            "target_id": target_id,
            "action_type": action_type,
            "old_rank": old_rank,
            "new_rank": new_rank,
            "observer_id": observer_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        if old_streak is not None:
            doc["old_streak"] = old_streak
        if source:
            doc["source"] = source
        await self.undo_logs.insert_one(doc)
        
    async def undo_last_action(self, target_id: int) -> tuple:
        """Undo the most recent rank change for a user. Returns (success, message)."""
        log = await self.undo_logs.find_one(
            {"target_id": target_id},
            sort=[("timestamp", -1)]
        )
        
        if not log:
            return False, "No actions found to undo for this user."
            
        old_rank = log.get("old_rank", "")
        
        # If they were unranked before, remove them. Otherwise, force set them back.
        if not old_rank:
            await self.remove_player_from_ladder(target_id, movement_source="undo")
            action_desc = "removed from the leaderboard (they were unranked before)"
        else:
            await self.force_set_player_rank(
                target_id,
                old_rank,
                bypass_unrank=True,
                is_undo=True,
                movement_source="undo",
            )
            action_desc = f"restored to **{old_rank}**"
        
        # Restore win streak if it was saved
        if "old_streak" in log:
            await self.player_ranks.update_one(
                {"user_id": target_id},
                {"$set": {"win_streak": log["old_streak"]}}
            )
            action_desc += f" (streak restored to {log['old_streak']})"
            
        # Delete the log so it can't be undone again
        await self.undo_logs.delete_one({"_id": log["_id"]})
        
        # Adjust wins/losses/matches if it was a match result
        if log.get("action_type") == "match_winner":
            await self.player_ranks.update_one(
                {"user_id": target_id},
                {"$inc": {"wins": -1, "matches": -1}}
            )
        elif log.get("action_type") == "match_loser":
            await self.player_ranks.update_one(
                {"user_id": target_id},
                {"$inc": {"losses": -1, "matches": -1}}
            )
            
        return True, action_desc
            
    async def get_ranking_config(self) -> dict:
        config = await self.db.bot_config.find_one({"_id": "ranking_setup"})
        return config or {}
        
    async def set_ranking_config(self, channel_id: int, message_id: int):
        await self.db.bot_config.update_one(
            {"_id": "ranking_setup"},
            {"$set": {"channel_id": channel_id, "message_id": message_id}},
            upsert=True
        )

