from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp
from pymongo import UpdateOne


CHALLONGE_BASE_URL = "https://api.challonge.com/v2.1"


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
        }

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
