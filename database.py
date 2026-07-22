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
        self.undo_logs = None
        self.bot_settings = None
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
            self.undo_logs = self.db.undo_logs
            self.bot_settings = self.db.bot_settings
            
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
                # New indexes for rapid history command execution
                await self.tickets.create_index([("user_id", 1), ("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                await self.tickets.create_index([("opponent_id", 1), ("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                
                await self.ranked_results.create_index("ticket_id")
                await self.observation_results.create_index("ticket_id")
            except Exception as e:
                print(f"Index creation note: {e}")
            
            return True
        except Exception as e:
            print(f"MongoDB connection error: {e}")
            return False
    
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
            
    async def log_undo_action(self, target_id: int, action_type: str, old_rank: str, new_rank: str, observer_id: Optional[int] = None, old_streak: Optional[int] = None):
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
            await self.remove_player_from_ladder(target_id)
            action_desc = "removed from the leaderboard (they were unranked before)"
        else:
            await self.force_set_player_rank(target_id, old_rank, bypass_unrank=True, is_undo=True)
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
        
        return True, action_desc
            
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
    
    async def reset_ranked_cooldown_only(self, user_id: int) -> bool:
        """Reset only the ranked cooldown (not observation). Used when a match is cancelled."""
        result = await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$unset": {"last_ranked_request": ""}}
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
        
    async def remove_player_from_ladder(self, user_id: int, is_undo: bool = False) -> bool:
        from ladder_utils import TIERS, parse_rank
        
        async with self.ladder_lock:
            player = await self.player_ranks.find_one({"user_id": user_id})
            if not player:
                return False
                
            old_rank = player.get("rank", "")
            await self.player_ranks.delete_one({"user_id": user_id})
            
            if not is_undo:
                await self.log_undo_action(user_id, "remove_player", old_rank, "")
            
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
        
    async def force_set_player_rank(self, user_id: int, target_rank: str, bypass_unrank: bool = False, is_undo: bool = False) -> tuple:
        from ladder_utils import TIERS, parse_rank
        
        parsed_target = parse_rank(target_rank)
        if not parsed_target or parsed_target[0] not in TIERS:
            return False, "Invalid rank format"
            
        # Check unrank cooldown (skip for admin bypass)
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
            
            new_actual_rank = f"{target_tier} {target_idx + 1}"
            
            if not is_undo:
                old_rank = player.get("rank", "") if player else ""
                await self.log_undo_action(user_id, "force_set_rank", old_rank, new_actual_rank)
                
            await self._bulk_reassign_ranks(tier_lists, TIERS)
            return True, new_actual_rank
        
    async def process_match_result(self, winner_id: int, loser_id: int) -> tuple:
        from ladder_utils import TIERS, parse_rank, get_sort_key
        
        async with self.ladder_lock:
            winner_rank = await self.get_player_rank(winner_id)
            loser_rank = await self.get_player_rank(loser_id)
            
            winner_key = get_sort_key(winner_rank)
            loser_key = get_sort_key(loser_rank)
            
            # Update win streaks before any early returns
            # Capture old streaks first for undo
            winner_doc = await self.player_ranks.find_one({"user_id": winner_id})
            loser_doc = await self.player_ranks.find_one({"user_id": loser_id})
            old_winner_streak = winner_doc.get("win_streak", 0) if winner_doc else 0
            old_loser_streak = loser_doc.get("win_streak", 0) if loser_doc else 0
            
            await self.player_ranks.update_one(
                {"user_id": winner_id},
                {"$inc": {"win_streak": 1}}
            )
            await self.player_ranks.update_one(
                {"user_id": loser_id},
                {"$set": {"win_streak": 0}}
            )
            
            if winner_key <= loser_key:
                # Log undo even for early return so streaks can be restored
                await self.log_undo_action(winner_id, "match_winner", winner_rank, winner_rank, old_streak=old_winner_streak)
                await self.log_undo_action(loser_id, "match_loser", loser_rank, loser_rank, old_streak=old_loser_streak)
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
            
            # Log undo action for both winner and loser (with streak data)
            await self.log_undo_action(winner_id, "match_winner", winner_rank, new_winner_rank, old_streak=old_winner_streak)
            await self.log_undo_action(loser_id, "match_loser", loser_rank, new_loser_rank, old_streak=old_loser_streak)
            
            return winner_rank, new_winner_rank, loser_rank, new_loser_rank
            
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
                           opponent_name: str, opponent_id: int, out_of_range: bool = False) -> int:
        ticket_id = await self._next_id("tickets")
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
            "ducking_ping_sent": False,
            "out_of_range": out_of_range
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
    
    async def mark_ducking_ping_sent(self, channel_id: int):
        await self.tickets.update_one(
            {"channel_id": channel_id},
            {"$set": {"ducking_ping_sent": True}}
        )
    
    async def add_ranked_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                winner_old: str, winner_new: str, loser_old: str, loser_new: str, winner_id: int, winner: str, note: Optional[str] = None):
        await self.ranked_results.delete_many({"ticket_id": ticket_id})
        result_id = await self._next_id("ranked_results")
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
    
    async def get_rematch_cooldown(self, user1_id: int, user2_id: int) -> float:
        """Returns hours left before these two players can face each other again, or 0 if allowed.
        Checks for the most recent closed Ranked 1v1 between them (in either direction)."""
        ticket = await self.tickets.find_one(
            {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [
                    {"user_id": user1_id, "opponent_id": user2_id},
                    {"user_id": user2_id, "opponent_id": user1_id}
                ]
            },
            sort=[("closed_at", -1)]
        )
        
        if not ticket or not ticket.get("closed_at"):
            return 0.0
            
        if ticket.get("rematch_cooldown_cleared"):
            return 0.0
            
        try:
            closed_at = datetime.fromisoformat(ticket["closed_at"])
            time_passed = (datetime.utcnow() - closed_at).total_seconds()
            cooldown_seconds = 24 * 3600  # 24 hours
            if time_passed < cooldown_seconds:
                return (cooldown_seconds - time_passed) / 3600.0
            return 0.0
        except ValueError:
            return 0.0
    
    async def reset_rematch_cooldown(self, user1_id: int, user2_id: int) -> bool:
        """Reset the rematch cooldown between two players by backdating the closed_at
        of their most recent match so the cooldown appears expired."""
        result = await self.tickets.update_many(
            {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [
                    {"user_id": user1_id, "opponent_id": user2_id},
                    {"user_id": user2_id, "opponent_id": user1_id}
                ]
            },
            {"$set": {"rematch_cooldown_cleared": True}},
        )
        return result.modified_count > 0
    
    async def get_h2h(self, player1_id: int, player2_id: int, limit: int = 10) -> Dict:
        """Get head-to-head stats between two players from ranked results."""
        # Find all closed ranked tickets between these two players (in either direction)
        pipeline = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [
                    {"user_id": player1_id, "opponent_id": player2_id},
                    {"user_id": player2_id, "opponent_id": player1_id}
                ]
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
        
        cursor = self.tickets.aggregate(pipeline)
        matches = await cursor.to_list(length=None)
        
        p1_wins = 0
        p2_wins = 0
        
        for match in matches:
            winner_id = match["result"].get("winner_id")
            if winner_id == player1_id:
                p1_wins += 1
            elif winner_id == player2_id:
                p2_wins += 1
        
        h2h_result = {
            "total": len(matches),
            "p1_wins": p1_wins,
            "p2_wins": p2_wins,
            "recent_matches": []
        }
        
        for doc in matches[:limit]:
            result = doc.pop("result")
            result["result_id"] = result.pop("id", None)
            result["result_created_at"] = result.pop("created_at", None)
            h2h_result["recent_matches"].append({**doc, **result})
        
        return h2h_result
    
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