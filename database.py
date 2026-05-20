import json
import aiohttp
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config

class Database:
    def __init__(self):
        self.token = Config.GIST_TOKEN
        self.gist_id = None
        self.data = {"tickets": [], "ranked_results": [], "observation_results": []}
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }
    
    async def init(self):
        """Load data from Gist or create new one"""
        if not self.token:
            print("Warning: GIST_TOKEN not set")
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                # Try to find existing gist
                url = "https://api.github.com/gists"
                async with session.get(url, headers=self.headers) as resp:
                    gists = await resp.json()
                    for gist in gists:
                        if "ticket-bot-data" in gist.get("description", ""):
                            self.gist_id = gist["id"]
                            files = gist.get("files", {})
                            if "data.json" in files:
                                raw_url = files["data.json"]["raw_url"]
                                async with session.get(raw_url) as data_resp:
                                    self.data = await data_resp.json()
                            print(f"Loaded existing gist: {self.gist_id}")
                            return
                
                # Create new gist if not found
                new_gist = {
                    "description": "ticket-bot-data",
                    "public": False,
                    "files": {
                        "data.json": {
                            "content": json.dumps(self.data)
                        }
                    }
                }
                async with session.post(url, json=new_gist, headers=self.headers) as resp:
                    result = await resp.json()
                    self.gist_id = result["id"]
                    print(f"Created new gist: {self.gist_id}")
        except Exception as e:
            print(f"Gist init error: {e}")
    
    async def _save(self):
        """Save data back to Gist"""
        if not self.gist_id or not self.token:
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.github.com/gists/{self.gist_id}"
                data = {
                    "files": {
                        "data.json": {
                            "content": json.dumps(self.data, default=str)
                        }
                    }
                }
                async with session.patch(url, json=data, headers=self.headers) as resp:
                    if resp.status != 200:
                        print(f"Save error: {resp.status}")
        except Exception as e:
            print(f"Save error: {e}")
    
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
                        entry = {**ticket, **result, "result_date": result["created_at"]}
                        ranked.append(entry)
                        break
                for result in self.data["observation_results"]:
                    if result["ticket_id"] == ticket["id"]:
                        entry = {**ticket, **result, "result_date": result["created_at"]}
                        obs.append(entry)
                        break
        
        ranked.sort(key=lambda x: x.get("closed_at", ""), reverse=True)
        obs.sort(key=lambda x: x.get("closed_at", ""), reverse=True)
        
        return {
            "ranked": ranked[:limit],
            "observations": obs[:limit]
        }
    
    async def clear_ranked_history(self, user_id: int) -> int:
        count = 0
        ids_to_delete = []
        
        for ticket in self.data["tickets"]:
            if ticket["user_id"] == user_id and ticket["ticket_type"] == "Ranked 1v1":
                ids_to_delete.append(ticket["id"])
        
        self.data["ranked_results"] = [r for r in self.data["ranked_results"] if r["ticket_id"] not in ids_to_delete]
        self.data["tickets"] = [t for t in self.data["tickets"] if t["id"] not in ids_to_delete]
        count = len(ids_to_delete)
        await self._save()
        return count
    
    async def clear_observation_history(self, user_id: int) -> int:
        count = 0
        ids_to_delete = []
        
        for ticket in self.data["tickets"]:
            if ticket["user_id"] == user_id and ticket["ticket_type"] == "Personal Observation":
                ids_to_delete.append(ticket["id"])
        
        self.data["observation_results"] = [r for r in self.data["observation_results"] if r["ticket_id"] not in ids_to_delete]
        self.data["tickets"] = [t for t in self.data["tickets"] if t["id"] not in ids_to_delete]
        count = len(ids_to_delete)
        await self._save()
        return count
    
    async def clear_all_history(self, user_id: int) -> tuple:
        ranked = await self.clear_ranked_history(user_id)
        obs = await self.clear_observation_history(user_id)
        return (ranked, obs)