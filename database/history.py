import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
import asyncio


class HistoryMixin:
    async def get_user_history(self, user_id: int, user_name: str, limit: int = 0) -> Dict[str, List]:
        # Use aggregation pipeline to join tickets with results in a single query
        # instead of fetching each result individually (N+1 problem)
        
        # Use two separate pipelines for user_id and opponent_id to avoid 
        # MongoDB's notoriously poor performance with $or combined with $sort.
        # This guarantees it will use the compound indexes we created.
        ranked_pipeline_user = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "user_id": user_id
            }},
            {"$sort": {"closed_at": -1}},
            {"$lookup": {
                "from": "ranked_results",
                "localField": "id",
                "foreignField": "ticket_id",
                "as": "result"
            }},
            {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}}
        ]
        
        ranked_pipeline_opp = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "opponent_id": user_id
            }},
            {"$sort": {"closed_at": -1}},
            {"$lookup": {
                "from": "ranked_results",
                "localField": "id",
                "foreignField": "ticket_id",
                "as": "result"
            }},
            {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}}
        ]
        
        obs_pipeline = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Personal Observation",
                "user_id": user_id
            }},
            {"$sort": {"closed_at": -1}},
            {"$lookup": {
                "from": "observation_results",
                "localField": "id",
                "foreignField": "ticket_id",
                "as": "result"
            }},
            {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}}
        ]
        
        if limit > 0:
            ranked_pipeline_user.append({"$limit": limit})
            ranked_pipeline_opp.append({"$limit": limit})
            obs_pipeline.append({"$limit": limit})
        
        ranked_cursor_user = self.tickets.aggregate(ranked_pipeline_user)
        ranked_cursor_opp = self.tickets.aggregate(ranked_pipeline_opp)
        obs_cursor = self.tickets.aggregate(obs_pipeline)
        
        fetch_len = None if limit <= 0 else limit
        ranked_raw_user, ranked_raw_opp, obs_raw = await asyncio.gather(
            ranked_cursor_user.to_list(length=fetch_len),
            ranked_cursor_opp.to_list(length=fetch_len),
            obs_cursor.to_list(length=fetch_len)
        )
        
        ranked_raw = ranked_raw_user + ranked_raw_opp
        # Deduplicate: prevent the same ticket from appearing twice
        # (e.g. if user_id == opponent_id due to data corruption, or edge cases)
        seen_ticket_ids = set()
        deduped = []
        for doc in ranked_raw:
            tid = doc.get("id")
            if tid not in seen_ticket_ids:
                seen_ticket_ids.add(tid)
                deduped.append(doc)
        ranked_raw = deduped
        ranked_raw.sort(key=lambda x: x.get("closed_at", ""), reverse=True)
        
        if limit > 0:
            ranked_raw = ranked_raw[:limit]
            
        # Fix field collision: both ticket and result have "id" and "created_at" fields.
        # Rename to avoid the result's fields silently overwriting the ticket's fields.
        ranked = []
        for doc in ranked_raw:
            result = doc.pop("result")
            result["result_id"] = result.pop("id", None)
            result["result_created_at"] = result.pop("created_at", None)
            ranked.append({**doc, **result})
        
        obs = []
        for doc in obs_raw:
            result = doc.pop("result")
            result["result_id"] = result.pop("id", None)
            result["result_created_at"] = result.pop("created_at", None)
            obs.append({**doc, **result})
        
        return {
            "ranked": ranked,
            "observations": obs
        }
    
    async def get_user_observation_count(self, user_id: int) -> int:
        """Returns the total number of closed Personal Observation tickets for a user."""
        count = await self.tickets.count_documents({
            "status": "closed",
            "ticket_type": "Personal Observation",
            "user_id": user_id
        })
        return count
    
