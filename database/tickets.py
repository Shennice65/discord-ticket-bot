import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from pymongo import ReturnDocument
import asyncio


class TicketsMixin:
    async def create_ticket(self, channel_id: int, user_id: int, ticket_type: str, 
                           opponent: Optional[str] = None, private_link: Optional[str] = None) -> int:
        ticket_id = await self._next_id("tickets")
        
        ticket = {
            "id": ticket_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "ticket_type": ticket_type,
            "status": "open",
            "created_at": str(datetime.utcnow()),
            "closed_at": None,
            "closed_by": None,
            "opponent_name": opponent,
            "opponent_id": None,
            "private_link": private_link,
            "ducking_ping_sent": False
        }
        await self.tickets.insert_one(ticket)
        print(f"Ticket {ticket_id} saved to MongoDB")
        return ticket_id
        
    async def create_ranked_ticket_db(self, channel_id: int, user_id: int, 
                           opponent_name: str, opponent_id: int, out_of_range: bool = False, status: str = "open") -> int:
        ticket_id = await self._next_id("tickets")
        ticket = {
            "id": ticket_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "ticket_type": "Ranked 1v1",
            "status": status,
            "created_at": str(datetime.utcnow()),
            "closed_at": None,
            "closed_by": None,
            "opponent_name": opponent_name,
            "opponent_id": opponent_id,
            "ducking_ping_sent": False,
            "out_of_range": out_of_range
        }
        await self.tickets.insert_one(ticket)
        return ticket_id
    
    async def close_ticket(self, channel_id: int, closed_by: int):
        result = await self.tickets.update_one(
            {"channel_id": channel_id, "status": {"$ne": "closed"}},
            {"$set": {
                "status": "closed",
                "closed_at": str(datetime.utcnow()),
                "closed_by": closed_by
            }, "$unset": {"processing_since": "", "processing_error": ""}}
        )
        return result.modified_count == 1

    async def claim_ticket_for_processing(self, channel_id: int) -> Optional[Dict]:
        """Atomically claim an open ticket so only one interaction can process it."""
        return await self.tickets.find_one_and_update(
            {"channel_id": channel_id, "status": "open"},
            {"$set": {
                "status": "processing",
                "processing_since": str(datetime.utcnow())
            }},
            return_document=ReturnDocument.BEFORE
        )

    async def finalize_processed_ticket(self, channel_id: int, closed_by: int) -> bool:
        """Close a claimed ticket without allowing stale interactions to overwrite state."""
        result = await self.tickets.update_one(
            {"channel_id": channel_id, "status": "processing"},
            {
                "$set": {
                    "status": "closed",
                    "closed_at": str(datetime.utcnow()),
                    "closed_by": closed_by
                },
                "$unset": {"processing_since": "", "processing_error": ""}
            }
        )
        return result.modified_count == 1

    async def mark_ticket_processing_failed(self, channel_id: int, error: Exception) -> None:
        """Quarantine a failed workflow instead of reopening it and replaying mutations."""
        await self.tickets.update_one(
            {"channel_id": channel_id, "status": "processing"},
            {"$set": {
                "status": "processing_failed",
                "processing_error": str(error)[:1000]
            }}
        )
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        return await self.tickets.find_one({"channel_id": channel_id})
    
    async def mark_ducking_ping_sent(self, channel_id: int):
        await self.tickets.update_one(
            {"channel_id": channel_id},
            {"$set": {"ducking_ping_sent": True}}
        )
    
    async def add_ranked_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                winner_old: str, winner_new: str, loser_old: str, loser_new: str, winner_id: int, winner: str, note: Optional[str] = None):
        existing = await self.ranked_results.find_one({"ticket_id": ticket_id}, {"id": 1})
        result_id = existing["id"] if existing else await self._next_id("ranked_results")
        result = {
            "id": result_id,
            "ticket_id": ticket_id,
            "observer_id": observer_id,
            "observer_name": observer_name,
            "winner_old": winner_old,
            "winner_new": winner_new,
            "loser_old": loser_old,
            "loser_new": loser_new,
            "starting_rank": winner_old, # backwards compatibility
            "ending_rank": winner_new,   # backwards compatibility
            "winner_id": winner_id,
            "winner": winner,
            "note": note,
            "created_at": str(datetime.utcnow())
        }
        await self.ranked_results.replace_one(
            {"ticket_id": ticket_id},
            result,
            upsert=True
        )
    
    async def add_observation_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                     starting_rank: str, ending_rank: str, note: Optional[str] = None):
        await self.observation_results.delete_many({"ticket_id": ticket_id})
        result_id = await self._next_id("observation_results")
        result = {
            "id": result_id,
            "ticket_id": ticket_id,
            "observer_id": observer_id,
            "observer_name": observer_name,
            "starting_rank": starting_rank,
            "ending_rank": ending_rank,
            "note": note,
            "created_at": str(datetime.utcnow())
        }
        await self.observation_results.insert_one(result)
    
