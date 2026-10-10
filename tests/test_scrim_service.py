import pytest
from pymongo.errors import DuplicateKeyError

from core.services.scrim_service import ScrimService, is_valid_private_link


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, length=None):
        return list(self.docs)


class FakeCollection:
    def __init__(self):
        self.docs = {}
        self._auto = 0

    @staticmethod
    def _match(doc, flt):
        for key, cond in flt.items():
            val = doc.get(key)
            if isinstance(cond, dict) and "$in" in cond:
                if val not in cond["$in"]:
                    return False
            elif val != cond:
                return False
        return True

    async def find_one(self, flt, *a, **k):
        for doc in self.docs.values():
            if self._match(doc, flt):
                return dict(doc)
        return None

    def find(self, flt, *a, **k):
        return FakeCursor([dict(d) for d in self.docs.values() if self._match(d, flt)])

    async def insert_one(self, doc):
        if "_id" not in doc:
            self._auto += 1
            doc = {**doc, "_id": self._auto}
        if doc["_id"] in self.docs:
            raise DuplicateKeyError("dup")
        self.docs[doc["_id"]] = dict(doc)

    async def update_one(self, flt, update, upsert=False):
        target = next((d for d in self.docs.values() if self._match(d, flt)), None)
        if target is None:
            if not upsert:
                return
            target = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            if "_id" not in target:
                self._auto += 1
                target["_id"] = self._auto
            self.docs[target["_id"]] = target
        target.update(update.get("$set", {}))


class FakeDB:
    def __init__(self):
        for name in ("scrim_sessions", "scrim_matches", "roblox_oauth_links", "roblox_usernames", "bot_settings", "config"):
            setattr(self, name, FakeCollection())


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def snapshot(phase="LOBBY", players=(), match_id=None, score=(0, 0), stats=()):
    return {
        "session_id": None,
        "phase": phase,
        "match_id": match_id,
        "score": {"Black": score[0], "White": score[1]},
        "players": [{"id": i, "name": f"p{i}", "display": f"P{i}", "team": None} for i in players],
        "stats": list(stats),
    }


@pytest.fixture
def env():
    db, clock = FakeDB(), Clock()
    return db, clock, ScrimService(db, clock)


async def start(svc):
    return await svc.start_session("https://www.roblox.com/games/1/x?privateServerLinkCode=abc", 1, 2)


def test_link_validation():
    assert is_valid_private_link("https://www.roblox.com/games/123/Timebomb?privateServerLinkCode=9988")
    assert is_valid_private_link("https://www.roblox.com/share?code=abcDEF123&type=Server")
    assert not is_valid_private_link("https://evil.com/games/123/x?privateServerLinkCode=1")
    assert not is_valid_private_link("https://www.roblox.com/games/123/x")
    assert not is_valid_private_link("")


@pytest.mark.asyncio
async def test_verified_ids_handles_string_and_int_roblox_ids(env):
    db, _, svc = env
    await db.roblox_oauth_links.insert_one({"_id": 1, "roblox_id": "111"})
    await db.roblox_oauth_links.insert_one({"_id": 2, "roblox_id": 222})
    assert await svc.verified_ids([111, 222, 333]) == [111, 222]
    assert await svc.verified_ids([]) == []


@pytest.mark.asyncio
async def test_sync_requires_active_session(env):
    _, _, svc = env
    result = await svc.handle_sync(snapshot())
    assert result.response["session"] == {"active": False}


@pytest.mark.asyncio
async def test_sync_returns_verified_subset_and_stores_snapshot(env):
    db, clock, svc = env
    session = await start(svc)
    await db.roblox_oauth_links.insert_one({"_id": 1, "roblox_id": "10"})
    payload = {**snapshot(players=[10, 20]), "session_id": session["session_id"]}
    result = await svc.handle_sync(payload)
    assert result.response["verified"] == [10]
    assert result.response["session"]["active"] is True
    stored = await svc.get_session()
    assert stored["snapshot"]["phase"] == "LOBBY"
    assert stored["last_sync_at"] == clock.t


@pytest.mark.asyncio
async def test_wrong_session_id_does_not_store_snapshot(env):
    _, _, svc = env
    await start(svc)
    result = await svc.handle_sync({**snapshot(players=[10]), "session_id": "stale"})
    assert result.response["verified"] == []
    assert (await svc.get_session())["snapshot"] is None


@pytest.mark.asyncio
async def test_final_recorded_once_and_acked(env):
    _, _, svc = env
    session = await start(svc)
    final = {"match_id": "m1", "score": {"Black": 7, "White": 3}, "winner": "Black", "rounds": 10, "stats": [{"UserId": 5, "Name": "a", "Team": "Black", "Kills": 4, "Deaths": 1, "Assists": 2}]}
    payload = {**snapshot("POSTGAME"), "session_id": session["session_id"], "finals": [final]}
    first = await svc.handle_sync(payload)
    again = await svc.handle_sync(payload)
    assert [m["_id"] for m in first.new_matches] == ["m1"]
    assert first.response["acked"] == ["m1"]
    assert again.new_matches == []
    assert again.response["acked"] == ["m1"]


@pytest.mark.asyncio
async def test_final_after_stop_is_still_recorded(env):
    _, _, svc = env
    session = await start(svc)
    await svc.stop_session()
    final = {"match_id": "m2", "score": {"Black": 7, "White": 0}, "winner": "Black", "stats": []}
    result = await svc.handle_sync({**snapshot("POSTGAME"), "session_id": session["session_id"], "finals": [final]})
    assert [m["_id"] for m in result.new_matches] == ["m2"]


@pytest.mark.asyncio
async def test_live_to_idle_without_final_records_aborted_match(env):
    _, _, svc = env
    session = await start(svc)
    sid = session["session_id"]
    stat = {"UserId": 5, "Name": "a", "Team": "Black", "Kills": 2, "Deaths": 1, "Assists": 0}
    await svc.handle_sync({**snapshot("LIVE", [5], "m3", (3, 2), [stat]), "session_id": sid})
    result = await svc.handle_sync({**snapshot("LOBBY"), "session_id": sid})
    assert len(result.new_matches) == 1
    aborted = result.new_matches[0]
    assert aborted["aborted"] is True and aborted["_id"] == "m3"
    assert aborted["score"] == {"Black": 3, "White": 2}


@pytest.mark.asyncio
async def test_live_to_postgame_with_final_is_not_aborted(env):
    _, _, svc = env
    session = await start(svc)
    sid = session["session_id"]
    await svc.handle_sync({**snapshot("LIVE", [5], "m4", (6, 2)), "session_id": sid})
    final = {"match_id": "m4", "score": {"Black": 7, "White": 2}, "winner": "Black", "stats": []}
    result = await svc.handle_sync({**snapshot("POSTGAME", [5], "m4", (7, 2)), "session_id": sid, "finals": [final]})
    assert [m["aborted"] for m in result.new_matches] == [False]


@pytest.mark.asyncio
async def test_host_offline_after_timeout(env):
    _, clock, svc = env
    session = await start(svc)
    await svc.handle_sync({**snapshot(), "session_id": session["session_id"]})
    clock.t += 10
    assert svc.host_offline(await svc.get_session()) is False
    clock.t += 15
    assert svc.host_offline(await svc.get_session()) is True


@pytest.mark.asyncio
async def test_config_bounds_and_channel_ids(env):
    db, _, svc = env
    assert (await svc.get_config())["min_players"] == 6
    assert await svc.set_config("min_players", 999) == 10
    assert (await svc.get_config())["min_players"] == 10
    await db.config.insert_one({"_id": "api_keys", "SCRIM_ANC_ID": "1558411207063638047", "SCRIM_LOG_ID": "1558482563297714356"})
    assert await svc.get_channel_ids() == (1558411207063638047, 1558482563297714356)


@pytest.mark.asyncio
async def test_offline_phase_reports_host_offline_immediately(env):
    _, _, svc = env
    session = await start(svc)
    await svc.handle_sync({**snapshot("OFFLINE"), "session_id": session["session_id"]})
    assert svc.host_offline(await svc.get_session()) is True


@pytest.mark.asyncio
async def test_timed_out_ids_survive_a_script_restart(env):
    _, _, svc = env
    session = await start(svc)
    sid = session["session_id"]
    await svc.handle_sync({**snapshot("LIVE", [5, 6], "m9"), "session_id": sid, "timed_out": [6, 9876543210]})
    assert (await svc.get_session())["timed_out"] == [6, 9876543210]

    # host script crashed and restarted: it has no session id yet, so the bot hands back who to release
    restarted = await svc.handle_sync({**snapshot("IDLE"), "session_id": None})
    assert restarted.response["timed_out"] == [6, 9876543210]
    assert (await svc.get_session())["timed_out"] == [6, 9876543210]  # not wiped until the script rejoins the session

    # once it adopts the session and reports an empty list, the record is cleared
    await svc.handle_sync({**snapshot("LOBBY"), "session_id": sid, "timed_out": []})
    assert (await svc.get_session())["timed_out"] == []


@pytest.mark.asyncio
async def test_timed_out_list_returned_after_stop(env):
    _, _, svc = env
    session = await start(svc)
    await svc.handle_sync({**snapshot("LIVE", [5], "m10"), "session_id": session["session_id"], "timed_out": [7]})
    await svc.stop_session()
    result = await svc.handle_sync({**snapshot("IDLE"), "session_id": None})
    assert result.response["session"] == {"active": False}
    assert result.response["timed_out"] == [7]


# ---- captain draft ---------------------------------------------------------
def draft_payload(session, pool_ids=(3, 4, 5, 6, 7), per_side=3, draft_id="d1", captains=None):
    caps = captains or {"Black": {"id": 1, "name": "capA"}, "White": {"id": 2, "name": "capB"}}
    return {
        **snapshot("DRAFT", [1, 2, *pool_ids]),
        "session_id": session["session_id"],
        "captains": caps,
        "draft": {"id": draft_id, "per_side": per_side, "pool": [{"id": i, "name": f"p{i}", "display": f"P{i}"} for i in pool_ids]},
    }


@pytest.mark.asyncio
async def test_draft_is_created_with_black_first_and_deadline(env):
    _, clock, svc = env
    session = await start(svc)
    result = await svc.handle_sync(draft_payload(session))
    assert result.response["draft"] == {"id": "d1", "picks": [], "done": False}
    draft = (await svc.get_session())["draft"]
    assert draft["turn"] == "Black" and draft["deadline"] == clock.t + 30
    assert draft["captains"]["Black"]["name"] == "capA"
    assert [p["id"] for p in draft["pool"]] == [3, 4, 5, 6, 7]


@pytest.mark.asyncio
async def test_picks_alternate_and_finish_when_teams_are_full(env):
    _, _, svc = env
    session = await start(svc)
    await svc.handle_sync(draft_payload(session))  # per_side 3 -> 2 picks each
    ok, _, draft = await svc.make_pick(3, "Black")
    assert ok and draft["turn"] == "White"
    assert (await svc.make_pick(4, "Black"))[0] is False  # not Black's turn
    assert (await svc.make_pick(3, "White"))[0] is False  # already taken
    for pid, team in ((4, "White"), (5, "Black"), (6, "White")):
        ok, _, draft = await svc.make_pick(pid, team)
        assert ok
    assert draft["done"] is True and draft["turn"] is None
    assert [p["team"] for p in draft["picks"]] == ["Black", "White", "Black", "White"]
    assert [p["id"] for p in draft["pool"]] == [7]
    synced = await svc.handle_sync(draft_payload(session))
    assert synced.response["draft"]["done"] is True
    assert [p["seq"] for p in synced.response["draft"]["picks"]] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_pool_comes_from_script_minus_picks(env):
    _, _, svc = env
    session = await start(svc)
    await svc.handle_sync(draft_payload(session))
    await svc.make_pick(3, "Black")
    # the script has not applied the pick yet and still reports 3; player 4 left the server
    await svc.handle_sync(draft_payload(session, pool_ids=(3, 5, 6, 7)))
    assert [p["id"] for p in (await svc.get_session())["draft"]["pool"]] == [5, 6, 7]


@pytest.mark.asyncio
async def test_expired_pick_is_made_randomly_and_turn_moves(env):
    _, clock, svc = env
    session = await start(svc)
    await svc.handle_sync(draft_payload(session))
    assert await svc.expire_pick() is None  # still within 30s
    clock.t += 31
    pick = await svc.expire_pick()
    assert pick["team"] == "Black" and pick["auto"] is True and pick["id"] in (3, 4, 5, 6, 7)
    draft = (await svc.get_session())["draft"]
    assert draft["turn"] == "White" and draft["deadline"] == clock.t + 30


@pytest.mark.asyncio
async def test_replaced_captain_resets_timer_for_their_turn(env):
    _, clock, svc = env
    session = await start(svc)
    await svc.handle_sync(draft_payload(session))
    clock.t += 20
    new_caps = {"Black": {"id": 9, "name": "newcap"}, "White": {"id": 2, "name": "capB"}}
    await svc.handle_sync(draft_payload(session, pool_ids=(3, 4, 5, 6), captains=new_caps))
    draft = (await svc.get_session())["draft"]
    assert draft["captains"]["Black"] == {"id": 9, "name": "newcap"}
    assert draft["deadline"] == clock.t + 30


@pytest.mark.asyncio
async def test_draft_with_empty_pool_finishes_immediately(env):
    _, _, svc = env
    session = await start(svc)
    result = await svc.handle_sync(draft_payload(session, pool_ids=(), per_side=1))
    assert result.response["draft"]["done"] is True


@pytest.mark.asyncio
async def test_draft_cleared_when_phase_leaves_draft(env):
    _, _, svc = env
    session = await start(svc)
    await svc.handle_sync(draft_payload(session))
    result = await svc.handle_sync({**snapshot("LIVE", [1, 2], "m1"), "session_id": session["session_id"]})
    assert "draft" not in result.response
    assert (await svc.get_session())["draft"] is None


@pytest.mark.asyncio
async def test_discord_id_for_roblox_matches_string_ids(env):
    db, _, svc = env
    await db.roblox_oauth_links.insert_one({"_id": 442188857014747136, "roblox_id": "9876543210"})
    assert await svc.discord_id_for_roblox(9876543210) == 442188857014747136
    assert await svc.discord_id_for_roblox(1) is None


@pytest.mark.asyncio
async def test_new_session_has_no_draft_and_config_has_draft_settings(env):
    _, _, svc = env
    session = await start(svc)
    assert session["draft"] is None
    cfg = await svc.get_config()
    assert cfg["captains"] == 1 and cfg["pick_seconds"] == 30


@pytest.mark.asyncio
async def test_bloxlink_cached_accounts_do_not_count_as_verified(env):
    db, _, svc = env
    # roblox_usernames also holds Bloxlink lookups; only OAuth links (roblox_oauth_links) prove ownership
    await db.roblox_usernames.insert_one({"_id": 5, "roblox_id": "555", "username": "bloxlinked"})
    await db.roblox_oauth_links.insert_one({"_id": 6, "roblox_id": "666"})
    assert await svc.verified_ids([555, 666]) == [666]
    assert await svc.discord_id_for_roblox(555) is None
    assert await svc.discord_id_for_roblox(666) == 6
