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

    async def get_user_ranked_stats(self, user_id: int, user_name: str = "") -> tuple[int, int, int, float]:
        """Return ranked totals without materializing the user's full history."""
        pipeline = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [{"user_id": user_id}, {"opponent_id": user_id}],
            }},
            {"$lookup": {
                "from": "ranked_results",
                "localField": "id",
                "foreignField": "ticket_id",
                "as": "result",
            }},
            {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}},
            {"$group": {
                "_id": None,
                "matches": {"$sum": 1},
                "wins": {"$sum": {"$cond": [
                    {"$or": [
                        {"$eq": ["$result.winner_id", user_id]},
                        {"$eq": [
                            {"$toLower": {"$ifNull": ["$result.winner", ""]}},
                            user_name.lower(),
                        ]},
                    ]},
                    1,
                    0,
                ]}},
            }},
        ]
        rows = await self.tickets.aggregate(pipeline).to_list(length=1)
        if not rows:
            return 0, 0, 0, 0.0
        matches = int(rows[0].get("matches", 0))
        wins = int(rows[0].get("wins", 0))
        losses = matches - wins
        return matches, wins, losses, (wins / matches) * 100 if matches else 0.0

    async def get_player_profile_stats(self, user_id: int, user_name: str = "") -> dict:
        """Returns comprehensive stats: matches, wins, losses, win_rate, current_streak, nemesis."""
        matches, wins, losses, win_rate = await self.get_user_ranked_stats(user_id, user_name)
        
        if matches == 0:
            return {
                "matches": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "current_streak": "None", "nemesis": None
            }

        # 1. Fetch recent matches to calculate streak
        history = await self.get_user_history(user_id, user_name, limit=20)
        ranked = history.get("ranked", [])
        
        current_streak = 0
        streak_type = None
        for match in ranked:
            is_win = (
                match.get("winner_id") == user_id or 
                (str(match.get("winner", "")).lower() == user_name.lower() and user_name)
            )
            match_type = "win" if is_win else "loss"
            
            if streak_type is None:
                streak_type = match_type
                current_streak = 1
            elif streak_type == match_type:
                current_streak += 1
            else:
                break
                
        streak_str = f"{current_streak} {streak_type}{'s' if current_streak != 1 else ''}"
        if current_streak >= 20 and len(ranked) == 20:
            streak_str = f"20+ {streak_type}s"
            
        # 2. Fetch Nemesis (opponent with most wins against this user)
        import re
        nemesis_pipeline = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [{"user_id": user_id}, {"opponent_id": user_id}],
            }},
            {"$lookup": {
                "from": "ranked_results",
                "localField": "id",
                "foreignField": "ticket_id",
                "as": "result",
            }},
            {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}},
            # Only keep matches where user lost
            {"$match": {
                "$nor": [
                    {"result.winner_id": user_id},
                    {"result.winner": re.compile(f"^{re.escape(user_name)}$", re.I) if user_name else "___NEVER_MATCH___"}
                ]
            }},
            {"$project": {
                "opponent": {"$cond": [{"$eq": ["$user_id", user_id]}, "$opponent_id", "$user_id"]},
                "opponent_name": {"$cond": [{"$eq": ["$user_id", user_id]}, "$opponent_name", "$user_name"]}
            }},
            {"$group": {
                "_id": "$opponent",
                "opponent_name": {"$first": "$opponent_name"},
                "wins_against_user": {"$sum": 1}
            }},
            {"$sort": {"wins_against_user": -1}},
            {"$limit": 1}
        ]
        
        nemesis_rows = await self.tickets.aggregate(nemesis_pipeline).to_list(length=1)
        nemesis = None
        if nemesis_rows:
            row = nemesis_rows[0]
            nemesis = {
                "user_id": row.get("_id"),
                "name": row.get("opponent_name"),
                "losses_to_them": row.get("wins_against_user")
            }
            
        return {
            "matches": matches,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "current_streak": streak_str,
            "nemesis": nemesis
        }

    async def get_user_observation_count(self, user_id: int) -> int:
        """Returns the total number of closed Personal Observation tickets for a user."""
        count = await self.tickets.count_documents({
            "status": "closed",
            "ticket_type": "Personal Observation",
            "user_id": user_id
        })
        return count

    async def get_top_winrates(self, min_matches: int = 3, limit: int = 10) -> List[Dict]:
        """Get the top players by win rate who have at least min_matches."""
        pipeline = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Ranked 1v1"
            }},
            {"$lookup": {
                "from": "ranked_results",
                "localField": "id",
                "foreignField": "ticket_id",
                "as": "result"
            }},
            {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}},
            {"$project": {
                "players": ["$user_id", "$opponent_id"],
                "winner_id": "$result.winner_id"
            }},
            {"$unwind": "$players"},
            {"$group": {
                "_id": "$players",
                "matches": {"$sum": 1},
                "wins": {
                    "$sum": {"$cond": [{"$eq": ["$players", "$winner_id"]}, 1, 0]}
                }
            }},
            {"$match": {
                "matches": {"$gte": min_matches},
                "wins": {"$gt": 0}
            }},
            {"$lookup": {
                "from": "player_ranks",
                "localField": "_id",
                "foreignField": "user_id",
                "as": "rank_info"
            }},
            {"$unwind": {"path": "$rank_info", "preserveNullAndEmptyArrays": True}},
            {"$match": {
                "rank_info.rank": {"$nin": [None, "Unranked"]}
            }},
            {"$addFields": {
                "losses": {"$subtract": ["$matches", "$wins"]},
                "win_rate": {"$multiply": [{"$divide": ["$wins", "$matches"]}, 100]}
            }},
            {"$sort": {"win_rate": -1, "matches": -1}},
            {"$limit": limit}
        ]
        
        cursor = self.tickets.aggregate(pipeline)
        return await cursor.to_list(length=None)

    async def get_observer_total_observations(self, observer_id: int) -> int:
        """Count all tickets (ranked + observation) this observer has refereed where ranks changed."""
        ranked_query = {
            "observer_id": observer_id,
            "$or": [
                {"$expr": {"$ne": ["$winner_old", "$winner_new"]}},
                {"$expr": {"$ne": ["$loser_old", "$loser_new"]}}
            ]
        }
        obs_query = {
            "observer_id": observer_id,
            "$expr": {"$ne": ["$starting_rank", "$ending_rank"]}
        }
        
        ranked_count, obs_count = await asyncio.gather(
            self.ranked_results.count_documents(ranked_query),
            self.observation_results.count_documents(obs_query),
        )
        return ranked_count + obs_count

    async def get_observer_last_active(self, observer_id: int) -> Optional[float]:
        """Get the most recent observation timestamp (unix) for this observer where ranks changed."""
        ranked_query = {
            "observer_id": observer_id,
            "$or": [
                {"$expr": {"$ne": ["$winner_old", "$winner_new"]}},
                {"$expr": {"$ne": ["$loser_old", "$loser_new"]}}
            ]
        }
        obs_query = {
            "observer_id": observer_id,
            "$expr": {"$ne": ["$starting_rank", "$ending_rank"]}
        }
        
        ranked_latest = await self.ranked_results.find_one(
            ranked_query,
            sort=[("created_at", -1)],
            projection={"created_at": 1}
        )
        obs_latest = await self.observation_results.find_one(
            obs_query,
            sort=[("created_at", -1)],
            projection={"created_at": 1}
        )
        
        timestamps = []
        for doc in (ranked_latest, obs_latest):
            if doc and "created_at" in doc:
                try:
                    # Format is like "2026-09-19 07:53:40.123456"
                    dt = datetime.strptime(doc["created_at"], "%Y-%m-%d %H:%M:%S.%f")
                    timestamps.append(dt.timestamp())
                except ValueError:
                    try:
                        # Fallback for old records without microseconds
                        dt = datetime.strptime(doc["created_at"], "%Y-%m-%d %H:%M:%S")
                        timestamps.append(dt.timestamp())
                    except ValueError:
                        pass
                        
        return max(timestamps) if timestamps else None
