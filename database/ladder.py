import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
import asyncio


def is_unranked_rank(rank: object) -> bool:
    """Return whether a stored rank represents a player entering the ladder."""
    return str(rank or "").strip().lower() in {"", "unranked"}


def calculate_rank_changes(current_players: list, tier_lists: dict, tiers: list) -> dict[int, int]:
    """Return real global ladder movement as old position minus new position."""
    from utils.ladder_utils import get_sort_key

    ranked_before = [
        (player["user_id"], get_sort_key(player.get("rank", "")))
        for player in current_players
        if get_sort_key(player.get("rank", ""))[0] != 99
    ]
    ranked_before.sort(key=lambda item: item[1])
    old_positions = {user_id: index for index, (user_id, _) in enumerate(ranked_before)}

    new_order = [user_id for tier in tiers for user_id in tier_lists.get(tier, [])]
    return {
        user_id: old_positions[user_id] - new_index if user_id in old_positions else 0
        for new_index, user_id in enumerate(new_order)
    }


def calculate_movement_metadata(
    player: dict,
    *,
    event_version: int,
    event_delta: int,
    event_role: str,
    movement_source: str,
) -> tuple[list[dict], int, str]:
    """Keep the latest event per badge source and aggregate their movement."""
    supported_sources = {"observation", "ranked", "admin_manual"}
    latest_by_source = {}
    for event in player.get("rank_change_events") or []:
        source = "admin_manual" if event.get("source") == "admin" else event.get("source")
        if source not in supported_sources:
            continue
        normalized_event = {**event, "source": source}
        previous = latest_by_source.get(source)
        if previous is None or int(normalized_event.get("version", 0)) >= int(previous.get("version", 0)):
            latest_by_source[source] = normalized_event

    latest_by_source[movement_source] = {
        "version": event_version,
        "delta": event_delta,
        "role": event_role,
        "source": movement_source,
    }
    history = sorted(latest_by_source.values(), key=lambda event: int(event.get("version", 0)))
    aggregate_delta = sum(int(event.get("delta", 0)) for event in history)
    movement_role = (
        "direct" if any(event.get("role") == "direct" for event in history)
        else ("passive" if aggregate_delta else "none")
    )
    return history, aggregate_delta, movement_role


class LadderMixin:
    async def get_all_player_ranks(self) -> list:
        cursor = self.player_ranks.find({})
        return await cursor.to_list(length=None)
        
    async def get_tier_count(self, tier: str) -> int:
        from utils.ladder_utils import parse_rank
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
        from utils.ladder_utils import get_sort_key
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
            val = player["last_ranked_request"]
            last_request = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
            time_passed = (datetime.utcnow() - last_request).total_seconds()
            cooldown_seconds = 24 * 3600
            if time_passed < cooldown_seconds:
                return (cooldown_seconds - time_passed) / 3600.0
            return 0.0
        except (ValueError, TypeError):
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
            val = player["last_obs_request"]
            last_request = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
            time_passed = (datetime.utcnow() - last_request).total_seconds()
            cooldown_seconds = 14 * 24 * 3600
            if time_passed < cooldown_seconds:
                return (cooldown_seconds - time_passed) / (24 * 3600.0)
            return 0.0
        except (ValueError, TypeError):
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
        
    async def _bulk_reassign_ranks(
        self,
        tier_lists: dict,
        tiers: list,
        *,
        direct_ids: set[int] | None = None,
        movement_source: str = "system",
    ) -> None:
        """Batch-update ranks and retain the latest movement from each badge source."""
        current_players = await self.player_ranks.find({}).to_list(length=None)
        players_by_id = {player["user_id"]: player for player in current_players}
        rank_changes = calculate_rank_changes(current_players, tier_lists, tiers)
        direct_ids = direct_ids or set()
        def is_newly_ranked(user_id: int) -> bool:
            return is_unranked_rank(players_by_id.get(user_id, {}).get("rank"))

        tracks_movement = (
            movement_source in {"ranked", "observation", "admin_manual"}
            and any(
                rank_changes.get(user_id, 0) > 0 or is_newly_ranked(user_id)
                for user_id in direct_ids
            )
        )
        event_version = None
        if tracks_movement:
            event_version_doc = await self.bot_settings.find_one_and_update(
                {"key": "ladder_event_version"},
                {"$inc": {"value": 1}},
                upsert=True,
                return_document=True,
            )
            event_version = event_version_doc["value"]
        ops = []
        now = str(datetime.utcnow())
        for t in tiers:
            for idx, uid in enumerate(tier_lists[t]):
                new_rank = f"{t} {idx + 1}"
                player = players_by_id.get(uid, {})
                fields = {"rank": new_rank, "updated_at": now}
                if tracks_movement:
                    event_delta = rank_changes.get(uid, 0)
                    event_role = "direct" if uid in direct_ids else ("passive" if event_delta else "none")
                    history, aggregate_delta, movement_role = calculate_movement_metadata(
                        player,
                        event_version=event_version,
                        event_delta=event_delta,
                        event_role=event_role,
                        movement_source=movement_source,
                    )
                    badge_source = next(
                        (
                            event.get("source")
                            for event in reversed(history)
                            if event.get("role") == "direct" and int(event.get("delta", 0)) > 0
                        ),
                        movement_source,
                    )
                    is_new = is_unranked_rank(player.get("rank"))
                    fields.update({
                        "rank_change": aggregate_delta,
                        "rank_change_events": history,
                        "movement_role": "new" if is_new else movement_role,
                        "movement_source": badge_source,
                        "movement_event_version": event_version,
                    })
                ops.append(UpdateOne(
                    {"user_id": uid},
                    {"$set": fields},
                    upsert=True
                ))
        if tracks_movement:
            # Ranked players are overwritten below; this clears unranked leftovers.
            await self.player_ranks.update_many({}, {"$set": {"rank_change": 0}})
        if ops:
            await self.player_ranks.bulk_write(ops, ordered=False)
    
    async def unrank_player(self, user_id: int, movement_source: str = "self_unrank") -> tuple:
        """Self-unrank: stores original rank, timestamp, removes from ladder."""
        from utils.ladder_utils import TIERS, parse_rank
        
        async with self.ladder_lock:
            player = await self.player_ranks.find_one({"user_id": user_id})
            if not player or not player.get("rank"):
                return False, "You are not currently ranked."
                
            if player.get("unranked_at"):
                return False, "You are already unranked."
                
            current_rank = player["rank"]

            await self.log_undo_action(user_id, "self_unrank", current_rank, "", source=movement_source)
            
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
                
            await self._bulk_reassign_ranks(
                tier_lists,
                TIERS,
                direct_ids={user_id},
                movement_source=movement_source,
            )
            return True, current_rank
        
    def _get_unrank_cooldown_days(self, player: dict) -> float:
        """Returns days left on unrank cooldown, or 0 if expired."""
        if not player or "unranked_at" not in player:
            return 0.0
        try:
            val = player["unranked_at"]
            unranked_at = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
            time_passed = (datetime.utcnow() - unranked_at).total_seconds()
            cooldown_seconds = 30 * 24 * 3600  # 1 month
            if time_passed < cooldown_seconds:
                return (cooldown_seconds - time_passed) / (24 * 3600.0)
            return 0.0
        except (ValueError, TypeError):
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
        from utils.ladder_utils import get_sort_key
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
            
    async def remove_player_from_ladder(self, user_id: int, is_undo: bool = False, movement_source: str = "admin_removal") -> bool:
        from utils.ladder_utils import TIERS, parse_rank
        
        async with self.ladder_lock:
            player = await self.player_ranks.find_one({"user_id": user_id})
            if not player:
                return False
                
            old_rank = player.get("rank", "")
            await self.player_ranks.delete_one({"user_id": user_id})
            
            if not is_undo:
                await self.log_undo_action(user_id, "remove_player", old_rank, "", source=movement_source)
            
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
                
            await self._bulk_reassign_ranks(
                tier_lists,
                TIERS,
                direct_ids={user_id},
                movement_source=movement_source,
            )
            return True
        
    async def force_set_player_rank(
        self,
        user_id: int,
        target_rank: str,
        bypass_unrank: bool = False,
        is_undo: bool = False,
        movement_source: str = "admin_manual",
    ) -> tuple:
        from utils.ladder_utils import TIERS, parse_rank
        
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
                await self.log_undo_action(
                    user_id,
                    "force_set_rank",
                    old_rank,
                    new_actual_rank,
                    source=movement_source,
                )
                
            await self._bulk_reassign_ranks(
                tier_lists,
                TIERS,
                direct_ids={user_id},
                movement_source=movement_source,
            )
            return True, new_actual_rank
        
    async def process_match_result(self, winner_id: int, loser_id: int) -> tuple:
        from utils.ladder_utils import TIERS, parse_rank, get_sort_key
        
        async with self.ladder_lock:
            # Every match replaces the previous movement display, even when
            # the result does not cause a ladder reorder.
            await self.player_ranks.update_many({}, {"$set": {"rank_change": 0}})
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
                {"$inc": {"win_streak": 1, "wins": 1, "matches": 1}}
            )
            await self.player_ranks.update_one(
                {"user_id": loser_id},
                {
                    "$set": {"win_streak": 0},
                    "$inc": {"losses": 1, "matches": 1}
                }
            )
            
            if winner_key <= loser_key:
                # Log undo even for early return so streaks can be restored
                await self.log_undo_action(winner_id, "match_winner", winner_rank, winner_rank, old_streak=old_winner_streak, source="ranked")
                await self.log_undo_action(loser_id, "match_loser", loser_rank, loser_rank, old_streak=old_loser_streak, source="ranked")
                all_players = await self.player_ranks.find({}).to_list(length=None)
                unchanged_tiers = {t: [] for t in TIERS}
                for player in all_players:
                    parsed = parse_rank(player.get("rank", ""))
                    if parsed and parsed[0] in unchanged_tiers:
                        unchanged_tiers[parsed[0]].append((player["user_id"], parsed[1]))
                for tier in TIERS:
                    unchanged_tiers[tier].sort(key=lambda item: item[1])
                    unchanged_tiers[tier] = [uid for uid, _ in unchanged_tiers[tier]]
                await self._bulk_reassign_ranks(
                    unchanged_tiers,
                    TIERS,
                    direct_ids={winner_id, loser_id},
                    movement_source="ranked",
                )
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
                    
            await self._bulk_reassign_ranks(
                tier_lists,
                TIERS,
                direct_ids={winner_id, loser_id},
                movement_source="ranked",
            )
            
            # Log undo action for both winner and loser (with streak data)
            await self.log_undo_action(winner_id, "match_winner", winner_rank, new_winner_rank, old_streak=old_winner_streak, source="ranked")
            await self.log_undo_action(loser_id, "match_loser", loser_rank, new_loser_rank, old_streak=old_loser_streak, source="ranked")
            
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
            val = ticket["closed_at"]
            closed_at = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
            time_passed = (datetime.utcnow() - closed_at).total_seconds()
            cooldown_seconds = 24 * 3600  # 24 hours
            if time_passed < cooldown_seconds:
                return (cooldown_seconds - time_passed) / 3600.0
            return 0.0
        except (ValueError, TypeError):
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
