import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
import asyncio


class AdminMixin:
    async def clear_unrank_penalty(self, user_id: int) -> bool:
        """Admin command to clear unrank restrictions."""
        result = await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$unset": {"original_rank": "", "unranked_at": ""}}
        )
        return result.modified_count > 0

    async def reset_all_timers(self, user_id: int) -> bool:
        """Resets ranked cooldown, obs cooldown, unrank penalty, and all rematch cooldowns for a user."""
        await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$unset": {
                "last_ranked_request": "", 
                "last_obs_request": "",
                "original_rank": "",
                "unranked_at": ""
            }}
        )
        
        await self.tickets.update_many(
            {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [
                    {"user_id": user_id},
                    {"opponent_id": user_id}
                ]
            },
            {"$set": {"rematch_cooldown_cleared": True}}
        )
        
        return True
        
    async def clear_ranked_history(self, user_id: int) -> int:
        tickets_cursor = self.tickets.find({"user_id": user_id, "ticket_type": "Ranked 1v1"})
        tickets = await tickets_cursor.to_list(length=None)
        
        if not tickets:
            return 0
            
        ticket_ids = [t["id"] for t in tickets]
        
        # Delete results
        await self.ranked_results.delete_many({"ticket_id": {"$in": ticket_ids}})
        # Delete tickets
        delete_result = await self.tickets.delete_many({"id": {"$in": ticket_ids}})
        
        return delete_result.deleted_count
    
    async def clear_observation_history(self, user_id: int) -> int:
        tickets_cursor = self.tickets.find({"user_id": user_id, "ticket_type": "Personal Observation"})
        tickets = await tickets_cursor.to_list(length=None)
        
        if not tickets:
            return 0
            
        ticket_ids = [t["id"] for t in tickets]
        
        # Delete results
        await self.observation_results.delete_many({"ticket_id": {"$in": ticket_ids}})
        # Delete tickets
        delete_result = await self.tickets.delete_many({"id": {"$in": ticket_ids}})
        
        return delete_result.deleted_count
    
    async def clear_all_history(self, user_id: int) -> tuple:
        ranked = await self.clear_ranked_history(user_id)
        obs = await self.clear_observation_history(user_id)
        return (ranked, obs)