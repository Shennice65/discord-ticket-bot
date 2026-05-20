import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config

class Database:
    def __init__(self):
        self.data_file = Config.DATA_FILE
        self.data = {"tickets": [], "ranked_results": [], "observation_results": []}
    
    async def init(self):
        """Load data from JSON file"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    self.data = json.load(f)
                print(f"Loaded {len(self.data['tickets'])} tickets from {self.data_file}")
            else:
                self._save_sync()
                print("Created new data file")
            return True
        except Exception as e:
            print(f"Data init error: {e}")
            return False
    
    def _save_sync(self):
        """Save data synchronously (for init)"""
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.data, f, default=str, indent=2)
        except Exception as e:
            print(f"Save error: {e}")
    
    async def _save(self):
        """Save data to file"""
        self._save_sync()
    
    async def create_ticket(self, channel_id: int, user_id: int, ticket_type: str, 
                           opponent: Optional[str] = None, private_link: Optional[str] = None) -> int:
        ticket_id = len(self.data["tickets"]) + 1
        ticket = {
            "id": ticket_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "ticket_type": ticket_type,
            "status": "open",
            "created_at": str(datetime.utcnow()),
            "closed_at": None,
            "closed_by": None,
            "opponent": opponent,
            "private_link": private_link
        }
        self.data["tickets"].append(ticket)
        await self._save()
        print(f"Ticket {ticket_id} saved to file")
        return ticket_id
    
    async def close_ticket(self, channel_id: int, closed_by: int):
        for ticket in self.data["tickets"]:
            if ticket["channel_id"] == channel_id:
                ticket["status"] = "closed"
                ticket["closed_at"] = str(datetime.utcnow())
                ticket["closed_by"] = closed_by
                break
        await self._save()
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        for ticket in self.data["tickets"]:
            if ticket["channel_id"] == channel_id:
                return ticket
        return None
    
    async def add_ranked_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                starting_rank: str, ending_rank: str, winner: str, note: Optional[str] = None):
        result = {
            "id": len(self.data["ranked_results"]) + 1,
            "ticket_id": ticket_id,
            "observer_id": observer_id,
            "observer_name": observer_name,
            "starting_rank": starting_rank,
            "ending_rank": ending_rank,
            "winner": winner,
            "note": note,
            "created_at": str(datetime.utcnow())
        }
        self.data["ranked_results"].append(result)
        await self._save()
    
    async def add_observation_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                     starting_rank: str, ending_rank: str, note: Optional[str] = None):
        result = {
            "id": len(self.data["observation_results"]) + 1,
            "ticket_id": ticket_id,
            "observer_id": observer_id,
            "observer_name": observer_name,
            "starting_rank": starting_rank,
            "ending_rank": ending_rank,
            "note": note,
            "created_at": str(datetime.utcnow())
        }
        self.data["observation_results"].append(result)
        await self._save()
    
    async def get_user_history(self, user_id: int, limit: int = 10) -> Dict[str, List]:
        ranked = []
        obs = []
        
        for ticket in self.data["tickets"]:
            if ticket["user_id"] == user_id and ticket["status"] == "closed":
                for result in self.data["ranked_results"]:
                    if result["ticket_id"] == ticket["id"]:
                        ranked.append({**ticket, **result, "result_date": result["created_at"]})
                        break
                for result in self.data["observation_results"]:
                    if result["ticket_id"] == ticket["id"]:
                        obs.append({**ticket, **result, "result_date": result["created_at"]})
                        break
        
        ranked.sort(key=lambda x: x.get("closed_at", ""), reverse=True)
        obs.sort(key=lambda x: x.get("closed_at", ""), reverse=True)
        
        return {
            "ranked": ranked[:limit],
            "observations": obs[:limit]
        }
    
    async def clear_ranked_history(self, user_id: int) -> int:
        ids_to_delete = []
        for ticket in self.data["tickets"]:
            if ticket["user_id"] == user_id and ticket["ticket_type"] == "Ranked 1v1":
                ids_to_delete.append(ticket["id"])
        
        self.data["ranked_results"] = [r for r in self.data["ranked_results"] if r["ticket_id"] not in ids_to_delete]
        self.data["tickets"] = [t for t in self.data["tickets"] if t["id"] not in ids_to_delete]
        await self._save()
        return len(ids_to_delete)
    
    async def clear_observation_history(self, user_id: int) -> int:
        ids_to_delete = []
        for ticket in self.data["tickets"]:
            if ticket["user_id"] == user_id and ticket["ticket_type"] == "Personal Observation":
                ids_to_delete.append(ticket["id"])
        
        self.data["observation_results"] = [r for r in self.data["observation_results"] if r["ticket_id"] not in ids_to_delete]
        self.data["tickets"] = [t for t in self.data["tickets"] if t["id"] not in ids_to_delete]
        await self._save()
        return len(ids_to_delete)
    
    async def clear_all_history(self, user_id: int) -> tuple:
        ranked = await self.clear_ranked_history(user_id)
        obs = await self.clear_observation_history(user_id)
        return (ranked, obs)