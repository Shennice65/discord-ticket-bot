import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
import asyncio


class LadderMixin:
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
        
    async def reset_obs_cooldown(self, user_id: int) -> bool:
        """Reset the personal observation cooldown for a user."""
        result = await self.player_ranks.update_one(
            {"user_id": user_id},
            {"$unset": {"last_obs_request": ""}}
        )
        return result.modified_count > 0
        
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
    
    async def is_player_self_unranked(self, user_id: int) -> bool:
        """Check if a player has deliberately unranked themselves (via the Unrank button).
        Returns False for players who were never ranked or are currently ranked."""
        player = await self.player_ranks.find_one({"user_id": user_id})
        if not player:
            return False  # Never ranked — not "self-unranked"
        return bool(player.get("unranked_at"))
        
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
        """Reset the rematch cooldown between two players by marking it cleared and backdating the closed_at."""
        result = await self.tickets.update_one(
            {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [
                    {"user_id": user1_id, "opponent_id": user2_id},
                    {"user_id": user2_id, "opponent_id": user1_id}
                ]
            },
            {"$set": {"rematch_cooldown_cleared": True, "closed_at": str(datetime(2000, 1, 1))}},
        )
        return result.modified_count > 0    
