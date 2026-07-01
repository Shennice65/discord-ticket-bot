import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
import asyncio

class Database:
    def __init__(self):
        self.uri = Config.MONGO_URI
        self.client = None
        self.db = None
        self.tickets = None
        self.ranked_results = None
        self.observation_results = None
        self.player_ranks = None
        self.ladder_lock = asyncio.Lock()
    
    async def init(self):
        """Connect to MongoDB"""
        try:
            if not self.uri:
                print("MONGO_URI not found in config! Please set it in .env.")
                return False
                
            import certifi
            self.client = AsyncIOMotorClient(self.uri, tlsCAFile=certifi.where())
            self.db = self.client.discord_bot_db
            
            self.tickets = self.db.tickets
            self.ranked_results = self.db.ranked_results
            self.observation_results = self.db.observation_results
            self.player_ranks = self.db.player_ranks
            
            # Simple ping to test connection
            await self.db.command('ping')
            print("Connected to MongoDB successfully!")
            
            # Create indexes for fast lookups
            try:
                # Drop old non-unique index if it exists before creating unique one
                existing = await self.player_ranks.index_information()
                if "user_id_1" in existing and not existing["user_id_1"].get("unique"):
                    await self.player_ranks.drop_index("user_id_1")
                await self.player_ranks.create_index("user_id", unique=True)
                await self.tickets.create_index("channel_id")
                await self.tickets.create_index([("status", 1), ("ticket_type", 1), ("user_id", 1)])
                await self.tickets.create_index([("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                await self.ranked_results.create_index("ticket_id")
                await self.observation_results.create_index("ticket_id")
            except Exception as e:
                print(f"Index creation note: {e}")
            
            return True
        except Exception as e:
            print(f"MongoDB connection error: {e}")
            return False
            
    async def get_all_player_ranks(self) -> list:
        cursor = self.player_ranks.find({})
        return await cursor.to_list(length=None)
        
    async def get_tier_count(self, tier: str) -> int:
        from ladder_utils import parse_rank
        all_players = await self.get_all_player_ranks()
        count = 0
        for p in all_players:
            rank_str = p.get("rank", "")
            parsed = parse_rank(rank_str)
            if parsed and parsed[0] == tier:
                count += 1
        return count
        
    async def get_ranking_config(self) -> dict:
        config = await self.db.bot_config.find_one({"_id": "ranking_setup"})
        return config or {}
        
    async def set_ranking_config(self, channel_id: int, message_id: int):
        await self.db.bot_config.update_one(
            {"_id": "ranking_setup"},
            {"$set": {"channel_id": channel_id, "message_id": message_id}},
            upsert=True
        )

    async def get_player_rank(self, user_id: int) -> str:
        player = await self.player_ranks.find_one({"user_id": user_id})
        return player.get("rank", "") if player else ""
    
    async def get_player_by_rank(self, rank_str: str) -> Optional[Dict]:
        """Look up a player by their exact rank string (e.g., 'Legends 3')"""
        player = await self.player_ranks.find_one({"rank": rank_str})
        return player
        
    async def get_global_rank_index(self, user_id: int) -> int:
        from ladder_utils import get_sort_key
        all_players = await self.player_ranks.find({}).to_list(length=None)
        
        valid_players = []
        for p in all_players:
            r = p.get("rank", "")
            key = get_sort_key(r)
            if key[0] != 99:
                valid_players.append((p["user_id"], key))
                
        valid_players.sort(key=lambda x: x[1])
        
        for idx, (uid, _) in enumerate(valid_players):
            if uid == user_id:
                return idx
                
        return -1 # Not found / unranked

    async def update_player_rank(self, user_id: int, rank: str):
        await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$set": {"rank": rank, "updated_at": str(datetime.utcnow())}},
            upsert=True
        )
        
    async def get_ranked_cooldown(self, user_id: int) -> float:
        """Returns hours left on cooldown, or 0 if they can request."""
        player = await self.player_ranks.find_one({"user_id": user_id})
        if not player or "last_ranked_request" not in player:
            return 0.0
            
        try:
            last_request = datetime.fromisoformat(player["last_ranked_request"])
            time_passed = (datetime.utcnow() - last_request).total_seconds()
            cooldown_seconds = 24 * 3600
            if time_passed < cooldown_seconds:
                return (cooldown_seconds - time_passed) / 3600.0
            return 0.0
        except ValueError:
            return 0.0
            
    async def update_ranked_cooldown(self, user_id: int):
        await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$set": {"last_ranked_request": str(datetime.utcnow())}},
            upsert=True
        )
        
    async def reset_ranked_cooldown(self, user_id: int) -> bool:
        result = await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$unset": {"last_ranked_request": "", "last_obs_request": ""}}
        )
        return result.modified_count > 0
        
    async def get_obs_cooldown(self, user_id: int) -> float:
        """Returns days left on cooldown, or 0 if they can request."""
        player = await self.player_ranks.find_one({"user_id": user_id})
        if not player or "last_obs_request" not in player:
            return 0.0
            
        try:
            last_request = datetime.fromisoformat(player["last_obs_request"])
            time_passed = (datetime.utcnow() - last_request).total_seconds()
            cooldown_seconds = 14 * 24 * 3600
            if time_passed < cooldown_seconds:
                return (cooldown_seconds - time_passed) / (24 * 3600.0)
            return 0.0
        except ValueError:
            return 0.0
            
    async def update_obs_cooldown(self, user_id: int):
        await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$set": {"last_obs_request": str(datetime.utcnow())}},
            upsert=True
        )
        
    async def _bulk_reassign_ranks(self, tier_lists: dict, tiers: list) -> None:
        """Batch-update all player ranks in a single bulk_write call."""
        ops = []
        now = str(datetime.utcnow())
        for t in tiers:
            for idx, uid in enumerate(tier_lists[t]):
                new_rank = f"{t} {idx + 1}"
                ops.append(UpdateOne(
                    {"user_id": uid},
                    {"$set": {"rank": new_rank, "updated_at": now}},
                    upsert=True
                ))
        if ops:
            await self.player_ranks.bulk_write(ops, ordered=False)
    
    async def unrank_player(self, user_id: int) -> tuple:
        """Self-unrank: stores original rank, timestamp, removes from ladder."""
        from ladder_utils import TIERS, parse_rank
        
        async with self.ladder_lock:
            player = await self.player_ranks.find_one({"user_id": user_id})
            if not player or not player.get("rank"):
                return False, "You are not currently ranked."
                
            if player.get("unranked_at"):
                return False, "You are already unranked."
                
            current_rank = player["rank"]
            
            await self.player_ranks.update_one(
                {"user_id": user_id},
                {"$set": {
                    "original_rank": current_rank,
                    "unranked_at": str(datetime.utcnow()),
                    "rank": ""
                }}
            )
            
            all_players = await self.player_ranks.find({}).to_list(length=None)
            tier_lists = {t: [] for t in TIERS}
            
            for p in all_players:
                if p["user_id"] == user_id:
                    continue
                rank_str = p.get("rank", "")
                parsed = parse_rank(rank_str)
                if parsed and parsed[0] in TIERS:
                    tier_lists[parsed[0]].append((p["user_id"], parsed[1]))
                    
            for t in TIERS:
                tier_lists[t].sort(key=lambda x: x[1])
                tier_lists[t] = [uid for uid, _ in tier_lists[t]]
                
            await self._bulk_reassign_ranks(tier_lists, TIERS)
            return True, current_rank
        
    def _get_unrank_cooldown_days(self, player: dict) -> float:
        """Returns days left on unrank cooldown, or 0 if expired."""
        if not player or "unranked_at" not in player:
            return 0.0
        try:
            unranked_at = datetime.fromisoformat(player["unranked_at"])
            time_passed = (datetime.utcnow() - unranked_at).total_seconds()
            cooldown_seconds = 30 * 24 * 3600  # 1 month
            if time_passed < cooldown_seconds:
                return (cooldown_seconds - time_passed) / (24 * 3600.0)
            return 0.0
        except ValueError:
            return 0.0
    
    async def get_unrank_cooldown(self, user_id: int) -> float:
        """Returns days left before player can be re-ranked."""
        player = await self.player_ranks.find_one({"user_id": user_id})
        return self._get_unrank_cooldown_days(player)
        
    async def can_player_r1(self, user_id: int) -> tuple:
        """Check if a formerly-ranked player can do R1s. Returns (allowed, reason)."""
        from ladder_utils import get_sort_key
        player = await self.player_ranks.find_one({"user_id": user_id})
        if not player:
            return True, ""  # Never ranked, no restriction
            
        original_rank = player.get("original_rank")
        if not original_rank:
            return True, ""  # Was never unranked, no restriction
            
        current_rank = player.get("rank", "")
        if not current_rank:
            return False, "You are currently unranked. You cannot request R1s until you are ranked back to your original rank."
            
        # Check if current rank is at or above (lower index = better) original rank
        current_key = get_sort_key(current_rank)
        original_key = get_sort_key(original_rank)
        if current_key <= original_key:
            return True, ""  # They are at or above their original rank
        else:
            return False, f"You must reach your original rank (**{original_rank}**) or higher before you can request R1s. You are currently **{current_rank}**."
            
    async def clear_unrank_penalty(self, user_id: int) -> bool:
        """Admin command to clear unrank restrictions."""
        result = await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$unset": {"original_rank": "", "unranked_at": ""}}
        )
        return result.modified_count > 0
        
    async def remove_player_from_ladder(self, user_id: int) -> bool:
        from ladder_utils import TIERS, parse_rank
        
        async with self.ladder_lock:
            player = await self.player_ranks.find_one({"user_id": user_id})
            if not player:
                return False
                
            await self.player_ranks.delete_one({"user_id": user_id})
            
            all_players = await self.player_ranks.find({}).to_list(length=None)
            tier_lists = {t: [] for t in TIERS}
            
            for p in all_players:
                rank_str = p.get("rank", "")
                parsed = parse_rank(rank_str)
                if parsed and parsed[0] in TIERS:
                    tier_lists[parsed[0]].append((p["user_id"], parsed[1]))
                    
            for t in TIERS:
                tier_lists[t].sort(key=lambda x: x[1])
                tier_lists[t] = [uid for uid, _ in tier_lists[t]]
                
            await self._bulk_reassign_ranks(tier_lists, TIERS)
            return True
        
    async def force_set_player_rank(self, user_id: int, target_rank: str, bypass_unrank: bool = False) -> tuple:
        from ladder_utils import TIERS, parse_rank
        
        parsed_target = parse_rank(target_rank)
        if not parsed_target or parsed_target[0] not in TIERS:
            return False, "Invalid rank format"
            
        # Check unrank cooldown (skip for admin bypass)
        if not bypass_unrank:
            player = await self.player_ranks.find_one({"user_id": user_id})
        parsed = parse_rank(target_rank)
        if not parsed:
            return False, "Invalid target rank format."
            
        target_tier, target_num = parsed
        if target_tier not in TIERS:
            return False, "Invalid tier."
            
        target_idx = target_num - 1
        
        async with self.ladder_lock:
            player = await self.player_ranks.find_one({"user_id": user_id})
            if player and player.get("unranked_at") and not bypass_unrank:
                return False, "Player is unranked. Use /clearunrank before re-ranking."
                
            await self.player_ranks.update_one(
                {"user_id": user_id},
                {"$set": {"unranked_at": None}},
                upsert=True
            )
            
            all_players = await self.player_ranks.find({}).to_list(length=None)
            tier_lists = {t: [] for t in TIERS}
            
            for p in all_players:
                rank_str = p.get("rank", "")
                p_parsed = parse_rank(rank_str)
                if p_parsed and p_parsed[0] in TIERS:
                    tier_lists[p_parsed[0]].append((p["user_id"], p_parsed[1]))
                    
            for t in TIERS:
                tier_lists[t].sort(key=lambda x: x[1])
                tier_lists[t] = [uid for uid, _ in tier_lists[t]]
                if user_id in tier_lists[t]:
                    tier_lists[t].remove(user_id)
                    
            if target_idx < 0:
                target_idx = 0
            tier_lists[target_tier].insert(target_idx, user_id)
            
            actual_new_rank = ""
            for t in TIERS:
                for idx, uid in enumerate(tier_lists[t]):
                    if uid == user_id:
                        actual_new_rank = f"{t} {idx + 1}"
                        
            await self._bulk_reassign_ranks(tier_lists, TIERS)
            return True, actual_new_rank
        
    async def process_match_result(self, winner_id: int, loser_id: int) -> tuple:
        from ladder_utils import TIERS, parse_rank, get_sort_key
        
        async with self.ladder_lock:
            winner_rank = await self.get_player_rank(winner_id)
            loser_rank = await self.get_player_rank(loser_id)
            
            winner_key = get_sort_key(winner_rank)
            loser_key = get_sort_key(loser_rank)
            
            if winner_key <= loser_key:
                return winner_rank, winner_rank, loser_rank, loser_rank
                
            all_players = await self.player_ranks.find({}).to_list(length=None)
            tier_lists = {t: [] for t in TIERS}
            
            for p in all_players:
                rank_str = p.get("rank", "")
                parsed = parse_rank(rank_str)
                if parsed and parsed[0] in TIERS:
                    tier_lists[parsed[0]].append((p["user_id"], parsed[1]))
                    
            for t in TIERS:
                tier_lists[t].sort(key=lambda x: x[1])
                tier_lists[t] = [uid for uid, num in tier_lists[t]]
                
            winner_parsed = parse_rank(winner_rank)
            if winner_parsed and winner_parsed[0] in tier_lists:
                if winner_id in tier_lists[winner_parsed[0]]:
                    tier_lists[winner_parsed[0]].remove(winner_id)
                    
            loser_parsed = parse_rank(loser_rank)
            if loser_parsed and loser_parsed[0] in tier_lists:
                try:
                    loser_idx = tier_lists[loser_parsed[0]].index(loser_id)
                    tier_lists[loser_parsed[0]].insert(loser_idx, winner_id)
                except ValueError:
                    tier_lists[loser_parsed[0]].append(winner_id)
            else:
                return winner_rank, winner_rank, loser_rank, loser_rank
                
            new_winner_rank = ""
            new_loser_rank = ""
            
            for t in TIERS:
                for idx, uid in enumerate(tier_lists[t]):
                    new_rank = f"{t} {idx + 1}"
                    if uid == winner_id:
                        new_winner_rank = new_rank
                    elif uid == loser_id:
                        new_loser_rank = new_rank
                    
            await self._bulk_reassign_ranks(tier_lists, TIERS)
            return winner_rank, new_winner_rank, loser_rank, new_loser_rank
            
    async def create_ticket(self, channel_id: int, user_id: int, ticket_type: str, 
                           opponent: Optional[str] = None, private_link: Optional[str] = None) -> int:
        # Generate an auto-incrementing-like ID using document count
        ticket_id = await self.tickets.count_documents({}) + 1
        
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
            "private_link": private_link
        }
        await self.tickets.insert_one(ticket)
        print(f"Ticket {ticket_id} saved to MongoDB")
        return ticket_id
        
    async def create_ranked_ticket_db(self, channel_id: int, user_id: int, 
                           opponent_name: str, opponent_id: int, private_link: Optional[str] = None) -> int:
        ticket_id = await self.tickets.count_documents({}) + 1
        ticket = {
            "id": ticket_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "ticket_type": "Ranked 1v1",
            "status": "open",
            "created_at": str(datetime.utcnow()),
            "closed_at": None,
            "closed_by": None,
            "opponent_name": opponent_name,
            "opponent_id": opponent_id,
            "private_link": private_link
        }
        await self.tickets.insert_one(ticket)
        return ticket_id
    
    async def close_ticket(self, channel_id: int, closed_by: int):
        await self.tickets.update_one(
            {"channel_id": channel_id},
            {"$set": {
                "status": "closed",
                "closed_at": str(datetime.utcnow()),
                "closed_by": closed_by
            }}
        )
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        return await self.tickets.find_one({"channel_id": channel_id})
    
    async def add_ranked_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                winner_old: str, winner_new: str, loser_old: str, loser_new: str, winner_id: int, winner: str, note: Optional[str] = None):
        result_id = await self.ranked_results.count_documents({}) + 1
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
        await self.ranked_results.insert_one(result)
    
    async def add_observation_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                     starting_rank: str, ending_rank: str, note: Optional[str] = None):
        result_id = await self.observation_results.count_documents({}) + 1
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
    
    async def get_user_history(self, user_id: int, user_name: str, limit: int = 10) -> Dict[str, List]:
        # Use aggregation pipeline to join tickets with results in a single query
        # instead of fetching each result individually (N+1 problem)
        
        ranked_pipeline = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [{"user_id": user_id}, {"opponent": user_name}]
            }},
            {"$sort": {"closed_at": -1}},
            {"$limit": limit},
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
                "$or": [{"user_id": user_id}, {"opponent": user_name}]
            }},
            {"$sort": {"closed_at": -1}},
            {"$limit": limit},
            {"$lookup": {
                "from": "observation_results",
                "localField": "id",
                "foreignField": "ticket_id",
                "as": "result"
            }},
            {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}}
        ]
        
        ranked_cursor = self.tickets.aggregate(ranked_pipeline)
        ranked_raw = await ranked_cursor.to_list(length=limit)
        
        obs_cursor = self.tickets.aggregate(obs_pipeline)
        obs_raw = await obs_cursor.to_list(length=limit)
        
        ranked = [{**doc, **doc.pop("result")} for doc in ranked_raw]
        obs = [{**doc, **doc.pop("result")} for doc in obs_raw]
        
        return {
            "ranked": ranked,
            "observations": obs
        }
    
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