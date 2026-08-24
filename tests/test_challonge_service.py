import asyncio
import sys
import types
from datetime import datetime, timezone

# The website test environment does not install the bot-only aiohttp package.
# These tests exercise pure normalization and Mongo persistence, not transport.
sys.modules.setdefault("aiohttp", types.SimpleNamespace(ClientError=Exception))

from core.services.challonge_service import (
    ChallongeService,
    ChallongeSnapshot,
    next_weekday_at,
    normalize_snapshot,
)


def sample_snapshot():
    return ChallongeSnapshot(
        tournament={
            "id": "9001",
            "type": "tournament",
            "attributes": {"name": "ATL S8", "url": "wtkpzw3b", "group_stage_enabled": True},
        },
        participants=[
            {"id": "101", "attributes": {"name": "Merleura", "group_id": 20, "states": {"active": True}}},
            {"id": "102", "attributes": {"name": "CherryBomb", "group_id": 20, "states": {"active": True}}},
            {"id": "201", "attributes": {"name": "Senpai", "group_id": 30, "states": {"active": True}}},
        ],
        matches=[
            {
                "id": "501",
                "attributes": {
                    "state": "open",
                    "round": 2,
                    "identifier": "A",
                    "group_id": 20,
                    "timestamps": {"starts_at": "2026-08-25T12:00:00Z"},
                    "relationships": {
                        "player1": {"data": {"id": "101", "type": "participant"}},
                        "player2": {"data": {"id": "102", "type": "participant"}},
                    },
                },
            },
            {
                "id": "502",
                "attributes": {
                    "state": "pending",
                    "round": 1,
                    "group_id": 30,
                    "relationships": {
                        "player1": {"data": {"id": "201", "type": "participant"}},
                        "player2": {"data": None},
                    },
                },
            },
        ],
    )


def test_normalize_snapshot_maps_groups_players_and_safe_market_states():
    participants, matches = normalize_snapshot(sample_snapshot())

    assert len(participants) == 3
    first = matches[0]
    assert first["team1_name"] == "Merleura"
    assert first["team2_name"] == "CherryBomb"
    assert first["group"] == "Group A"
    assert first["challonge_state"] == "open"
    assert first["initial_market_state"] == "upcoming"
    assert first["scheduled_at"].isoformat() == "2026-08-25T12:00:00+00:00"

    waiting = matches[1]
    assert waiting["group"] == "Group B"
    assert waiting["team2_name"] == "TBD"
    assert waiting["initial_market_state"] == "pending"


class FakeCollection:
    def __init__(self):
        self.operations = []
        self.updates = []

    async def bulk_write(self, operations, ordered=False):
        self.operations.extend(operations)

    async def update_many(self, query, update):
        self.updates.append((query, update))

    async def update_one(self, query, update):
        self.updates.append((query, update))


class FakeMongoDatabase:
    def __init__(self):
        self.config = FakeCollection()
        self.challonge_participants = FakeCollection()


class FakeDatabase:
    def __init__(self):
        self.db = FakeMongoDatabase()
        self.betting_matches = FakeCollection()


def test_sync_preserves_local_market_control_and_missing_schedule():
    snapshot = sample_snapshot()
    snapshot.matches[1]["attributes"].pop("timestamps", None)
    database = FakeDatabase()
    service = ChallongeService(database)

    async def skip_default_schedule(*args, **kwargs):
        return 0

    service.apply_default_weekend_schedule = skip_default_schedule
    result = asyncio.run(service.sync_snapshot(snapshot))

    assert result["matches"] == 2
    open_match_update = database.betting_matches.operations[0]._doc
    assert open_match_update["$setOnInsert"]["state"] == "upcoming"
    assert "state" not in open_match_update["$set"]

    unscheduled_update = database.betting_matches.operations[1]._doc
    assert "scheduled_at" not in unscheduled_update["$set"]
    assert database.db.config.updates[0][1]["$set"]["CHALLONGE_TOURNAMENT_ID"] == "9001"


def test_vietnam_weekend_slots_are_saturday_and_sunday_at_8pm():
    friday = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    saturday = next_weekday_at(friday, weekday=5, hour=20, timezone_name="Asia/Ho_Chi_Minh")
    sunday = next_weekday_at(friday, weekday=6, hour=20, timezone_name="Asia/Ho_Chi_Minh")

    assert saturday.isoformat() == "2026-08-22T13:00:00+00:00"
    assert sunday.isoformat() == "2026-08-23T13:00:00+00:00"
