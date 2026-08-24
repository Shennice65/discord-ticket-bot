from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from pymongo import UpdateOne


CHALLONGE_BASE_URL = "https://api.challonge.com/v2.1"
DEFAULT_BETTING_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_MATCH_HOUR = 20
DEFAULT_LOCK_MINUTES = 5
GROUP_WEEKDAYS = {"Group A": 5, "Group B": 6}  # Monday=0


class ChallongeError(RuntimeError):
    """A user-safe Challonge integration error."""


@dataclass
class ChallongeSnapshot:
    tournament: dict[str, Any]
    participants: list[dict[str, Any]]
    matches: list[dict[str, Any]]


def _resource_attributes(resource: dict[str, Any]) -> dict[str, Any]:
    return resource.get("attributes") or {}


def _relationship_id(resource: dict[str, Any], name: str) -> str | None:
    attributes = _resource_attributes(resource)
    relationships = resource.get("relationships") or attributes.get("relationships") or {}
    relationship = relationships.get(name) or {}
    data = relationship.get("data")
    if isinstance(data, dict) and data.get("id") is not None:
        return str(data["id"])
    return None


def _group_sort_key(group_id: str) -> tuple[int, int | str]:
    try:
        return (0, int(group_id))
    except (TypeError, ValueError):
        return (1, group_id)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def next_weekday_at(after: datetime, *, weekday: int, hour: int, timezone_name: str) -> datetime:
    """Return the next local weekday/hour strictly after the supplied instant."""
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        if timezone_name in {"Asia/Ho_Chi_Minh", "Asia/Saigon"}:
            # Vietnam is UTC+7 year-round. This keeps Windows development
            # environments working even when the optional IANA tzdata package
            # is absent; production Linux still uses ZoneInfo normally.
            local_timezone = timezone(timedelta(hours=7), name="ICT")
        else:
            raise ChallongeError(f"Unknown BETTING_TIMEZONE: {timezone_name}") from error
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    local_after = after.astimezone(local_timezone)
    days_ahead = (weekday - local_after.weekday()) % 7
    candidate_date = local_after.date() + timedelta(days=days_ahead)
    candidate = datetime.combine(candidate_date, time(hour=hour), tzinfo=local_timezone)
    if candidate <= local_after:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def normalize_snapshot(snapshot: ChallongeSnapshot) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert JSON:API resources into stable local participant/match records."""
    participant_rows: list[dict[str, Any]] = []
    participant_names: dict[str, str] = {}
    participant_groups: dict[str, str | None] = {}

    for resource in snapshot.participants:
        participant_id = str(resource.get("id"))
        attributes = _resource_attributes(resource)
        name = str(attributes.get("name") or attributes.get("username") or f"Participant {participant_id}")
        group_id = attributes.get("group_id")
        normalized_group_id = str(group_id) if group_id is not None else None
        participant_names[participant_id] = name
        participant_groups[participant_id] = normalized_group_id
        participant_rows.append(
            {
                "challonge_participant_id": participant_id,
                "name": name,
                "username": attributes.get("username"),
                "seed": attributes.get("seed"),
                "group_id": normalized_group_id,
                "active": bool((attributes.get("states") or {}).get("active", True)),
                "final_rank": attributes.get("final_rank"),
            }
        )

    explicit_groups = {
        str(attributes.get("group_id"))
        for resource in snapshot.matches
        for attributes in [_resource_attributes(resource)]
        if attributes.get("group_id") is not None
    }
    explicit_groups.update(group_id for group_id in participant_groups.values() if group_id is not None)
    group_labels = {
        group_id: f"Group {chr(65 + index)}" if index < 26 else f"Group {index + 1}"
        for index, group_id in enumerate(sorted(explicit_groups, key=_group_sort_key))
    }

    match_rows: list[dict[str, Any]] = []
    for resource in snapshot.matches:
        attributes = _resource_attributes(resource)
        player1_id = _relationship_id(resource, "player1")
        player2_id = _relationship_id(resource, "player2")
        group_id_value = attributes.get("group_id")
        group_id = str(group_id_value) if group_id_value is not None else None
        if group_id is None and player1_id and participant_groups.get(player1_id) == participant_groups.get(player2_id):
            group_id = participant_groups.get(player1_id)

        timestamps = attributes.get("timestamps") or {}
        scheduled_at = _parse_timestamp(
            attributes.get("scheduled_at")
            or attributes.get("starts_at")
            or timestamps.get("scheduled_at")
            or timestamps.get("starts_at")
        )
        challonge_state = str(attributes.get("state") or "pending").lower()
        if challonge_state == "complete":
            initial_market_state = "completed"
        elif player1_id and player2_id:
            initial_market_state = "upcoming"
        else:
            initial_market_state = "pending"

        round_number = attributes.get("round")
        stage_name = group_labels.get(group_id, "Final Stage" if group_id is None else "Group Stage")
        round_label = f"Round {round_number}" if round_number is not None else None
        match_rows.append(
            {
                "challonge_match_id": str(resource.get("id")),
                "player1_id": player1_id,
                "player2_id": player2_id,
                "team1_name": participant_names.get(player1_id, "TBD"),
                "team2_name": participant_names.get(player2_id, "TBD"),
                "group_id": group_id,
                "group": stage_name,
                "round": round_number,
                "round_label": round_label,
                "identifier": attributes.get("identifier"),
                "challonge_state": challonge_state,
                "winner_id": str(attributes["winner_id"]) if attributes.get("winner_id") is not None else None,
                "scores": attributes.get("scores"),
                "scheduled_at": scheduled_at,
                "initial_market_state": initial_market_state,
            }
        )

    return participant_rows, match_rows


class ChallongeService:
    def __init__(self, database):
        self.database = database

    async def _configuration(self) -> dict[str, Any]:
        config = await self.database.db.config.find_one({"_id": "api_keys"}) or {}
        api_key = str(config.get("CHALLONGE_API_KEY") or "").strip()
        slug = str(config.get("CHALLONGE_TOURNAMENT_SLUG") or "").strip()
        auth_type = str(config.get("CHALLONGE_AUTH_TYPE") or "v1").strip().lower()
        tournament_id = str(config.get("CHALLONGE_TOURNAMENT_ID") or "").strip() or None
        if not api_key:
            raise ChallongeError("CHALLONGE_API_KEY is missing from MongoDB config.")
        if not slug and not tournament_id:
            raise ChallongeError("CHALLONGE_TOURNAMENT_SLUG is missing from MongoDB config.")
        if auth_type not in {"v1", "v2"}:
            raise ChallongeError("CHALLONGE_AUTH_TYPE must be either v1 or v2.")
        return {
            "api_key": api_key,
            "slug": slug,
            "auth_type": auth_type,
            "tournament_id": tournament_id,
        }

    @staticmethod
    def _headers(config: dict[str, Any]) -> dict[str, str]:
        authorization = config["api_key"]
        if config["auth_type"] == "v2" and not authorization.lower().startswith("bearer "):
            authorization = f"Bearer {authorization}"
        return {
            "Accept": "application/json",
            "Content-Type": "application/vnd.api+json",
            "Authorization-Type": config["auth_type"],
            "Authorization": authorization,
        }

    async def _get(self, session: aiohttp.ClientSession, path: str, *, params: dict | None = None) -> dict:
        url = f"{CHALLONGE_BASE_URL}{path}"
        try:
            async with session.get(url, params=params) as response:
                if response.status == 401:
                    raise ChallongeError("Challonge rejected the API key (401 Unauthorized).")
                if response.status == 403:
                    raise ChallongeError("The Challonge account cannot access this tournament (403 Forbidden).")
                if response.status == 404:
                    raise ChallongeError("The configured Challonge tournament was not found (404).")
                if response.status == 429:
                    raise ChallongeError("The Challonge API request limit has been reached (429).")
                if response.status >= 400:
                    raise ChallongeError(f"Challonge returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
                if not isinstance(payload, dict):
                    raise ChallongeError("Challonge returned an unexpected response format.")
                return payload
        except asyncio.TimeoutError as error:
            raise ChallongeError("The Challonge API timed out.") from error
        except aiohttp.ClientError as error:
            raise ChallongeError("Could not connect to the Challonge API.") from error

    async def _find_tournament(self, session: aiohttp.ClientSession, config: dict[str, Any]) -> dict[str, Any]:
        if config["tournament_id"]:
            payload = await self._get(session, f"/tournaments/{config['tournament_id']}.json")
            return payload.get("data") or {}

        payload = await self._get(session, "/tournaments.json", params={"page": 1, "per_page": 100})
        tournament = next(
            (
                resource
                for resource in payload.get("data") or []
                if str(_resource_attributes(resource).get("url") or "") == config["slug"]
            ),
            None,
        )
        if not tournament:
            raise ChallongeError(
                f"Tournament slug {config['slug']} was not found in the account's first 100 tournaments."
            )
        return tournament

    async def fetch_snapshot(self) -> ChallongeSnapshot:
        config = await self._configuration()
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(headers=self._headers(config), timeout=timeout, trust_env=False) as session:
            tournament = await self._find_tournament(session, config)
            tournament_id = str(tournament.get("id") or "")
            if not tournament_id:
                raise ChallongeError("Challonge did not return a tournament ID.")
            participant_payload, match_payload = await asyncio.gather(
                self._get(session, f"/tournaments/{tournament_id}/participants.json", params={"page": 1, "per_page": 100}),
                self._get(session, f"/tournaments/{tournament_id}/matches.json", params={"page": 1, "per_page": 100}),
            )
        return ChallongeSnapshot(
            tournament=tournament,
            participants=list(participant_payload.get("data") or []),
            matches=list(match_payload.get("data") or []),
        )

    async def sync_snapshot(self, snapshot: ChallongeSnapshot) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        tournament_id = str(snapshot.tournament.get("id"))
        tournament_attributes = _resource_attributes(snapshot.tournament)
        participant_rows, match_rows = normalize_snapshot(snapshot)

        participant_operations = [
            UpdateOne(
                {
                    "tournament_id": tournament_id,
                    "challonge_participant_id": row["challonge_participant_id"],
                },
                {"$set": {**row, "tournament_id": tournament_id, "synced_at": now}},
                upsert=True,
            )
            for row in participant_rows
        ]
        if participant_operations:
            await self.database.db.challonge_participants.bulk_write(participant_operations, ordered=False)

        match_operations = []
        for row in match_rows:
            synced_fields = {key: value for key, value in row.items() if key != "initial_market_state"}
            # Challonge often has no schedule. Preserve an administrator's local
            # schedule rather than replacing it with null during later syncs.
            if synced_fields.get("scheduled_at") is None:
                synced_fields.pop("scheduled_at", None)
            match_operations.append(
                UpdateOne(
                    {"challonge_match_id": row["challonge_match_id"]},
                    {
                        "$set": {
                            **synced_fields,
                            "tournament_id": tournament_id,
                            "tournament_name": tournament_attributes.get("name"),
                            "source_missing": False,
                            "synced_at": now,
                        },
                        "$setOnInsert": {
                            "state": row["initial_market_state"],
                            "team1_pool": 0,
                            "team2_pool": 0,
                            "bettor_count": 0,
                            "created_at": now,
                        },
                    },
                    upsert=True,
                )
            )
        if match_operations:
            await self.database.betting_matches.bulk_write(match_operations, ordered=False)

        settled_matches = await self._settle_completed_matches(match_rows, now=now)

        scheduled_matches = await self.apply_default_weekend_schedule(tournament_id, now=now)

        current_match_ids = [row["challonge_match_id"] for row in match_rows]
        if current_match_ids:
            await self.database.betting_matches.update_many(
                {
                    "tournament_id": tournament_id,
                    "challonge_match_id": {"$nin": current_match_ids},
                },
                {"$set": {"source_missing": True, "synced_at": now}},
            )

        await self.database.db.config.update_one(
            {"_id": "api_keys"},
            {"$set": {"CHALLONGE_TOURNAMENT_ID": tournament_id}},
        )
        return {
            "tournament_id": tournament_id,
            "tournament_name": tournament_attributes.get("name") or "Unknown tournament",
            "group_stage_enabled": bool(tournament_attributes.get("group_stage_enabled")),
            "participants": len(participant_rows),
            "matches": len(match_rows),
            "playable_matches": sum(1 for row in match_rows if row["player1_id"] and row["player2_id"]),
            "open_matches": sum(1 for row in match_rows if row["challonge_state"] == "open"),
            "completed_matches": sum(1 for row in match_rows if row["challonge_state"] == "complete"),
            "scheduled_matches": scheduled_matches,
            "settled_matches": settled_matches,
        }

    async def _settle_completed_matches(self, match_rows: list[dict[str, Any]], *, now: datetime) -> int:
        """Settle each Challonge-confirmed result once in a Mongo transaction."""
        completed = [row for row in match_rows if row["challonge_state"] == "complete"]
        settled_count = 0
        for row in completed:
            async with await self.database.client.start_session() as session:
                async with session.start_transaction():
                    match = await self.database.betting_matches.find_one(
                        {
                            "challonge_match_id": row["challonge_match_id"],
                            "settlement_status": {"$ne": "settled"},
                        },
                        session=session,
                    )
                    if not match:
                        continue
                    stable_match_id = str(match.get("challonge_match_id") or match.get("_id"))
                    wagers = await self.database.betting_wagers.find(
                        {"match_id": stable_match_id, "status": "active"},
                        session=session,
                    ).to_list(length=10000)
                    winner_id = str(row.get("winner_id") or "")
                    valid_winner = winner_id in {
                        str(match.get("player1_id")),
                        str(match.get("player2_id")),
                    }
                    winning_wagers = [wager for wager in wagers if str(wager.get("team_id")) == winner_id]

                    # A draw/void or an empty winning side cannot produce fair
                    # parimutuel odds, so every active stake is returned.
                    if not valid_winner or (wagers and not winning_wagers):
                        for wager in wagers:
                            stake = int(wager.get("stake", 0))
                            if stake:
                                await self.database.wallets.update_one(
                                    {"user_id": wager["user_id"]},
                                    {"$inc": {"balance": stake}, "$set": {"updated_at": now}},
                                    session=session,
                                )
                                await self.database.wallet_transactions.insert_one(
                                    {
                                        "_id": str(uuid.uuid4()),
                                        "user_id": wager["user_id"],
                                        "match_id": stable_match_id,
                                        "type": "wager_refund",
                                        "amount": stake,
                                        "created_at": now,
                                    },
                                    session=session,
                                )
                            await self.database.betting_wagers.update_one(
                                {"_id": wager["_id"]},
                                {"$set": {"status": "refunded", "payout": stake, "settled_at": now}},
                                session=session,
                            )
                    else:
                        total_pool = sum(int(wager.get("stake", 0)) for wager in wagers)
                        winners = winning_wagers
                        winning_pool = sum(int(wager.get("stake", 0)) for wager in winners)
                        payouts: dict[str, int] = {}
                        if total_pool and winning_pool:
                            remainders = []
                            paid = 0
                            for wager in winners:
                                base, remainder = divmod(total_pool * int(wager["stake"]), winning_pool)
                                payouts[str(wager["_id"])] = base
                                paid += base
                                remainders.append((remainder, str(wager["_id"])))
                            remainders.sort(key=lambda item: (-item[0], item[1]))
                            for _, wager_id in remainders[: total_pool - paid]:
                                payouts[wager_id] += 1

                        for wager in wagers:
                            payout = payouts.get(str(wager["_id"]), 0)
                            status = "won" if payout else "lost"
                            if payout:
                                await self.database.wallets.update_one(
                                    {"user_id": wager["user_id"]},
                                    {"$inc": {"balance": payout}, "$set": {"updated_at": now}},
                                    session=session,
                                )
                                await self.database.wallet_transactions.insert_one(
                                    {
                                        "_id": str(uuid.uuid4()),
                                        "user_id": wager["user_id"],
                                        "match_id": stable_match_id,
                                        "type": "wager_payout",
                                        "amount": payout,
                                        "created_at": now,
                                    },
                                    session=session,
                                )
                            await self.database.betting_wagers.update_one(
                                {"_id": wager["_id"]},
                                {"$set": {"status": status, "payout": payout, "settled_at": now}},
                                session=session,
                            )

                    result = await self.database.betting_matches.update_one(
                        {"_id": match["_id"], "settlement_status": {"$ne": "settled"}},
                        {
                            "$set": {
                                "state": "completed",
                                "winner_id": row.get("winner_id"),
                                "scores": row.get("scores"),
                                "settlement_status": "settled",
                                "settled_at": now,
                                "updated_at": now,
                            }
                        },
                        session=session,
                    )
                    if result.modified_count:
                        settled_count += 1
        return settled_count

    async def apply_default_weekend_schedule(self, tournament_id: str, *, now: datetime | None = None) -> int:
        """Schedule one Group A match Saturday and one Group B match Sunday each week."""
        config = await self.database.db.config.find_one({"_id": "api_keys"}) or {}
        timezone_name = str(config.get("BETTING_TIMEZONE") or DEFAULT_BETTING_TIMEZONE)
        try:
            match_hour = int(config.get("BETTING_MATCH_HOUR", DEFAULT_MATCH_HOUR))
            lock_minutes = int(config.get("BETTING_LOCK_MINUTES", DEFAULT_LOCK_MINUTES))
        except (TypeError, ValueError) as error:
            raise ChallongeError("BETTING_MATCH_HOUR and BETTING_LOCK_MINUTES must be numbers.") from error
        if not 0 <= match_hour <= 23:
            raise ChallongeError("BETTING_MATCH_HOUR must be between 0 and 23.")
        if not 0 <= lock_minutes <= 1440:
            raise ChallongeError("BETTING_LOCK_MINUTES must be between 0 and 1440.")

        now = now or datetime.now(timezone.utc)
        scheduled_count = 0
        for group_name, weekday in GROUP_WEEKDAYS.items():
            cursor = self.database.betting_matches.find(
                {
                    "tournament_id": tournament_id,
                    "group": group_name,
                    "state": {"$in": ["upcoming", "open", "locked"]},
                    "challonge_state": {"$ne": "complete"},
                    "player1_id": {"$nin": [None, ""]},
                    "player2_id": {"$nin": [None, ""]},
                }
            ).sort([("round", 1), ("identifier", 1), ("challonge_match_id", 1)])
            matches = await cursor.to_list(length=200)
            existing_times = [match.get("scheduled_at") for match in matches if match.get("scheduled_at")]
            anchor = now
            if existing_times:
                latest = max(
                    value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
                    for value in existing_times
                )
                if latest >= anchor:
                    anchor = latest + timedelta(seconds=1)

            for match in matches:
                if match.get("scheduled_at"):
                    continue
                scheduled_at = next_weekday_at(
                    anchor,
                    weekday=weekday,
                    hour=match_hour,
                    timezone_name=timezone_name,
                )
                betting_closes_at = scheduled_at - timedelta(minutes=lock_minutes)
                result = await self.database.betting_matches.update_one(
                    {"_id": match["_id"], "scheduled_at": {"$in": [None]}},
                    {
                        "$set": {
                            "scheduled_at": scheduled_at,
                            "betting_closes_at": betting_closes_at,
                            "schedule_source": "default_weekend",
                            "updated_at": now,
                        }
                    },
                )
                if result.modified_count:
                    scheduled_count += 1
                    anchor = scheduled_at + timedelta(seconds=1)
        return scheduled_count

    @staticmethod
    def snapshot_status(snapshot: ChallongeSnapshot) -> dict[str, Any]:
        participant_rows, match_rows = normalize_snapshot(snapshot)
        attributes = _resource_attributes(snapshot.tournament)
        return {
            "tournament_id": str(snapshot.tournament.get("id")),
            "tournament_name": attributes.get("name") or "Unknown tournament",
            "group_stage_enabled": bool(attributes.get("group_stage_enabled")),
            "participants": len(participant_rows),
            "matches": len(match_rows),
            "playable_matches": sum(1 for row in match_rows if row["player1_id"] and row["player2_id"]),
            "open_matches": sum(1 for row in match_rows if row["challonge_state"] == "open"),
            "completed_matches": sum(1 for row in match_rows if row["challonge_state"] == "complete"),
        }
