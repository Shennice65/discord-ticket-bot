"""Scrim session state, verification lookup and match recording.

The Roblox host script is the real-time referee (countdown, kicks, rounds). This service is the
persistent side: it stores the latest snapshot the script reports, answers which players are
OAuth-verified, and records finished or aborted matches exactly once.
"""
import asyncio
import random
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from core.services.scrim_rules import draft_next_turn

PHASES = {"IDLE", "LOBBY", "COUNTDOWN", "CAPTAINS", "DRAFT", "LIVE", "POSTGAME", "BREAK", "OFFLINE"}
TEAMS = ("Black", "White")
SESSION_ID = "active"
HOST_TIMEOUT_SECONDS = 20

# key -> (default, min, max)
CONFIG_SPEC: Dict[str, Tuple[int, int, int]] = {
    "min_players": (6, 2, 10),
    "break_seconds": (300, 30, 3600),
    "verify_grace_seconds": (20, 0, 120),
    "countdown_seconds": (30, 10, 120),
    "captains": (1, 0, 1),  # 1 = two bomb rounds pick captains who draft; 0 = random teams
    "pick_seconds": (30, 10, 120),
}
CONFIG_TTL = 30
CHANNEL_TTL = 60

_PRIVATE_LINK_RE = re.compile(
    r"^https://(www\.)?roblox\.com/games/\d+/[^?#]*\?[^#]*privateServerLinkCode=[\w-]+", re.IGNORECASE
)
_SHARE_LINK_RE = re.compile(r"^https://(www\.)?roblox\.com/share\?[^#]*code=[\w-]+", re.IGNORECASE)


def is_valid_private_link(link: str) -> bool:
    link = (link or "").strip()
    return bool(len(link) < 500 and (_PRIVATE_LINK_RE.match(link) or _SHARE_LINK_RE.match(link)))


def _int(value: Any, default: int = 0, lo: int = 0, hi: int = 2**63 - 1) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _text(value: Any, limit: int = 40) -> str:
    return str(value if value is not None else "")[:limit]


def _team(value: Any) -> Optional[str]:
    return value if value in TEAMS else None


def clean_player(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    uid = _int(raw.get("id"), 0, 0)
    if uid <= 0:
        return None
    return {"id": uid, "name": _text(raw.get("name")), "display": _text(raw.get("display")), "team": _team(raw.get("team"))}


def clean_stat(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    return {
        "UserId": _int(raw.get("UserId"), 0, 0),
        "Name": _text(raw.get("Name")),
        "Team": _team(raw.get("Team")) or "No Team",
        "Kills": _int(raw.get("Kills"), 0, 0, 999),
        "Deaths": _int(raw.get("Deaths"), 0, 0, 999),
        "Assists": _int(raw.get("Assists"), 0, 0, 999),
    }


def clean_captains(raw: Any) -> dict:
    out = {}
    if isinstance(raw, dict):
        for team in TEAMS:
            c = raw.get(team)
            if isinstance(c, dict) and _int(c.get("id"), 0, 0) > 0:
                out[team] = {"id": _int(c.get("id"), 0, 0), "name": _text(c.get("name"))}
    return out


def clean_draft(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    draft_id = _text(raw.get("id"), 64)
    if not draft_id:
        return None
    pool = []
    for item in (raw.get("pool") or [])[:30]:
        player = clean_player(item)
        if player:
            pool.append({"id": player["id"], "name": player["name"], "display": player["display"]})
    return {"id": draft_id, "per_side": _int(raw.get("per_side"), 0, 0, 5), "pool": pool}


def clean_snapshot(payload: dict) -> dict:
    phase = payload.get("phase")
    score = payload.get("score") if isinstance(payload.get("score"), dict) else {}
    players = [p for p in (clean_player(x) for x in (payload.get("players") or [])[:60]) if p]
    stats = [s for s in (clean_stat(x) for x in (payload.get("stats") or [])[:30]) if s]
    ends = payload.get("phase_ends")
    timed_out = sorted({_int(x, 0, 0) for x in (payload.get("timed_out") or [])[:60]} - {0})
    return {
        "phase": phase if phase in PHASES else "IDLE",
        "phase_ends": _int(ends, 0, 0) or None,
        "match_id": _text(payload.get("match_id"), 64) or None,
        "score": {t: _int(score.get(t), 0, 0, 99) for t in TEAMS},
        "players": players,
        "stats": stats,
        "rounds": _int(payload.get("rounds"), 0, 0, 99),
        "timed_out": timed_out,
        "captain_round": _int(payload.get("captain_round"), 0, 0, 2),
        "captains": clean_captains(payload.get("captains")),
        "draft": clean_draft(payload.get("draft")),
    }


def clean_final(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    match_id = _text(raw.get("match_id"), 64)
    if not match_id:
        return None
    score = raw.get("score") if isinstance(raw.get("score"), dict) else {}
    stats = [s for s in (clean_stat(x) for x in (raw.get("stats") or [])[:30]) if s]
    return {
        "match_id": match_id,
        "score": {t: _int(score.get(t), 0, 0, 99) for t in TEAMS},
        "winner": _team(raw.get("winner")),
        "forfeit": bool(raw.get("forfeit")),
        "rounds": _int(raw.get("rounds"), 0, 0, 99),
        "stats": stats,
    }


class SyncResult:
    def __init__(self, response: dict, new_matches: List[dict]):
        self.response = response
        self.new_matches = new_matches


class ScrimService:
    def __init__(self, db, clock: Callable[[], float] = time.time):
        self.db = db
        self.clock = clock
        self._cfg_cache: Optional[Tuple[float, dict]] = None
        self._chan_cache: Optional[Tuple[float, Tuple[Optional[int], Optional[int]]]] = None
        self._draft_lock = asyncio.Lock()

    # ---- configuration -------------------------------------------------
    async def get_config(self) -> dict:
        now = self.clock()
        if self._cfg_cache and now - self._cfg_cache[0] < CONFIG_TTL:
            return dict(self._cfg_cache[1])
        cfg = {}
        for key, (default, lo, hi) in CONFIG_SPEC.items():
            doc = await self.db.bot_settings.find_one({"key": f"scrim_{key}"})
            cfg[key] = _int(doc.get("value") if doc else default, default, lo, hi)
        self._cfg_cache = (now, cfg)
        return dict(cfg)

    async def set_config(self, key: str, value: int) -> int:
        default, lo, hi = CONFIG_SPEC[key]
        value = _int(value, default, lo, hi)
        await self.db.bot_settings.update_one({"key": f"scrim_{key}"}, {"$set": {"value": value}}, upsert=True)
        self._cfg_cache = None
        return value

    async def get_channel_ids(self) -> Tuple[Optional[int], Optional[int]]:
        """(lobby/announcement channel, log channel) from config.api_keys."""
        now = self.clock()
        if self._chan_cache and now - self._chan_cache[0] < CHANNEL_TTL:
            return self._chan_cache[1]
        doc = await self.db.config.find_one({"_id": "api_keys"}) or {}
        snowflake_max = 2**63 - 1
        ids = (
            _int(doc.get("SCRIM_ANC_ID"), 0, 0, snowflake_max) or None,
            _int(doc.get("SCRIM_LOG_ID"), 0, 0, snowflake_max) or None,
        )
        self._chan_cache = (now, ids)
        return ids

    # ---- session -------------------------------------------------------
    async def get_session(self) -> Optional[dict]:
        return await self.db.scrim_sessions.find_one({"_id": SESSION_ID})

    async def start_session(self, link: str, user_id: int, channel_id: int) -> dict:
        session = {
            "session_id": uuid.uuid4().hex,
            "active": True,
            "link": link,
            "started_by": user_id,
            "channel_id": channel_id,
            "message_id": None,
            "started_at": self.clock(),
            "snapshot": None,
            "last_sync_at": None,
            "host_offline_notified": False,
            "draft": None,
        }
        await self.db.scrim_sessions.update_one({"_id": SESSION_ID}, {"$set": session}, upsert=True)
        return session

    async def attach_message(self, message_id: int) -> None:
        await self.db.scrim_sessions.update_one({"_id": SESSION_ID}, {"$set": {"message_id": message_id}})

    async def stop_session(self) -> Optional[dict]:
        session = await self.get_session()
        if not session or not session.get("active"):
            return session
        await self.db.scrim_sessions.update_one({"_id": SESSION_ID}, {"$set": {"active": False}})
        session["active"] = False
        return session

    async def mark_host_offline_notified(self) -> None:
        await self.db.scrim_sessions.update_one({"_id": SESSION_ID}, {"$set": {"host_offline_notified": True}})

    def host_offline(self, session: Optional[dict]) -> bool:
        if not session or not session.get("active"):
            return False
        if (session.get("snapshot") or {}).get("phase") == "OFFLINE":
            return True
        last = session.get("last_sync_at")
        base = last if last is not None else session.get("started_at", 0)
        return self.clock() - base > HOST_TIMEOUT_SECONDS

    # ---- verification --------------------------------------------------
    async def verified_ids(self, ids: List[int]) -> List[int]:
        """Roblox UserIds that completed the Roblox OAuth login (roblox_oauth_links, written only by the OAuth callback).

        roblox_usernames is deliberately not used: it also caches Bloxlink lookups, which are not OAuth proof.
        roblox_id is stored as a string by the callback."""
        ids = sorted({int(i) for i in ids if int(i) > 0})
        if not ids:
            return []
        candidates: List[Any] = ids + [str(i) for i in ids]
        cursor = self.db.roblox_oauth_links.find({"roblox_id": {"$in": candidates}}, {"roblox_id": 1})
        docs = await cursor.to_list(length=len(candidates))
        found = set()
        for doc in docs:
            try:
                found.add(int(doc.get("roblox_id")))
            except (TypeError, ValueError):
                continue
        return sorted(found)

    # ---- match recording -----------------------------------------------
    async def record_match(self, final: dict, session_id: Optional[str], aborted: bool = False) -> Optional[dict]:
        """Insert a match once. Returns the stored doc, or None if this match_id was already recorded."""
        doc = {
            "_id": final["match_id"],
            "session_id": session_id,
            "score": final["score"],
            "winner": final["winner"],
            "forfeit": final["forfeit"],
            "aborted": aborted,
            "rounds": final["rounds"],
            "stats": final["stats"],
            "ended_at": datetime.now(timezone.utc),
        }
        try:
            await self.db.scrim_matches.insert_one(doc)
        except DuplicateKeyError:
            return None
        return doc

    # ---- script sync ---------------------------------------------------
    async def handle_sync(self, payload: dict) -> SyncResult:
        session = await self.get_session()
        finals = [f for f in (clean_final(x) for x in (payload.get("finals") or [])[:5]) if f]
        sid = _text(payload.get("session_id"), 64)
        cfg = await self.get_config()

        new_matches: List[dict] = []
        acked: List[str] = []
        for final in finals:
            # Finals are accepted even after /scrim stop so a finished match is never lost.
            stored = await self.record_match(final, session.get("session_id") if session else None)
            if stored:
                new_matches.append(stored)
            acked.append(final["match_id"])

        if not session or not session.get("active"):
            leftover = (session or {}).get("timed_out") or []
            return SyncResult(
                {"session": {"active": False}, "verified": [], "config": cfg, "acked": acked, "timed_out": leftover},
                new_matches,
            )

        response: Dict[str, Any] = {
            "session": {"id": session["session_id"], "active": True},
            "config": cfg,
            "acked": acked,
        }
        if sid != session["session_id"]:
            # Script has not picked up the current session yet; do not store its snapshot.
            response["verified"] = []
            # A script that just (re)started must release anyone a previous run left timed out.
            response["timed_out"] = session.get("timed_out") or []
            return SyncResult(response, new_matches)

        snapshot = clean_snapshot(payload)
        previous = session.get("snapshot") or {}
        aborted = await self._abort_if_needed(session, previous, snapshot, {f["match_id"] for f in finals})
        if aborted:
            new_matches.append(aborted)

        verified = await self.verified_ids([p["id"] for p in snapshot["players"]])
        snapshot["verified"] = verified
        await self.db.scrim_sessions.update_one(
            {"_id": SESSION_ID},
            {
                "$set": {
                    "snapshot": snapshot,
                    "timed_out": snapshot["timed_out"],
                    "last_sync_at": self.clock(),
                    "host_offline_notified": False,
                }
            },
        )
        response["verified"] = verified
        draft_response = await self._sync_draft(snapshot)
        if draft_response:
            response["draft"] = draft_response
        return SyncResult(response, new_matches)

    async def _abort_if_needed(self, session: dict, previous: dict, snapshot: dict, finals_now: set) -> Optional[dict]:
        match_id = previous.get("match_id")
        if previous.get("phase") != "LIVE" or not match_id:
            return None
        still_live = snapshot["phase"] == "LIVE" and snapshot["match_id"] == match_id
        if still_live or match_id in finals_now:
            return None
        if await self.db.scrim_matches.find_one({"_id": match_id}):
            return None
        final = {
            "match_id": match_id,
            "score": previous.get("score") or {t: 0 for t in TEAMS},
            "winner": None,
            "forfeit": False,
            "rounds": previous.get("rounds", 0),
            "stats": previous.get("stats") or [],
        }
        return await self.record_match(final, session.get("session_id"), aborted=True)

    # ---- captain draft ---------------------------------------------------
    async def _save_draft(self, draft: Optional[dict]) -> None:
        await self.db.scrim_sessions.update_one({"_id": SESSION_ID}, {"$set": {"draft": draft}})

    async def _sync_draft(self, snapshot: dict) -> Optional[dict]:
        """Create/refresh the draft from what the host script sees. The script reports pool + captains;
        the bot owns who has been picked."""
        async with self._draft_lock:
            session = await self.get_session() or {}
            existing = session.get("draft")
            incoming = snapshot.get("draft")
            if snapshot["phase"] != "DRAFT" or not incoming:
                if existing:
                    await self._save_draft(None)
                return None

            now = self.clock()
            pick_seconds = (await self.get_config())["pick_seconds"]
            captains = snapshot["captains"]
            if not existing or existing["id"] != incoming["id"]:
                turn = draft_next_turn(0, 0, incoming["per_side"])
                draft = {
                    "id": incoming["id"],
                    "per_side": incoming["per_side"],
                    "captains": captains,
                    "pool": incoming["pool"],
                    "picks": [],
                    "turn": turn,
                    "deadline": now + pick_seconds if turn else None,
                    "done": turn is None or not incoming["pool"],
                }
            else:
                draft = existing
                picked = {p["id"] for p in draft["picks"]}
                draft["pool"] = [p for p in incoming["pool"] if p["id"] not in picked]
                for team in TEAMS:
                    old = (draft["captains"] or {}).get(team) or {}
                    new = captains.get(team)
                    if new and old.get("id") != new["id"]:
                        draft["captains"][team] = new  # captain left and the script replaced them
                        if draft["turn"] == team and not draft["done"]:
                            draft["deadline"] = now + pick_seconds
                if not draft["done"] and (draft["turn"] is None or not draft["pool"]):
                    draft["done"], draft["turn"], draft["deadline"] = True, None, None
            await self._save_draft(draft)
            return {"id": draft["id"], "picks": draft["picks"], "done": draft["done"]}

    async def make_pick(self, player_id: int, team: str, auto: bool = False) -> Tuple[bool, str, Optional[dict]]:
        async with self._draft_lock:
            return await self._apply_pick(player_id, team, auto)

    async def _apply_pick(self, player_id: int, team: str, auto: bool) -> Tuple[bool, str, Optional[dict]]:
        session = await self.get_session() or {}
        draft = session.get("draft")
        if not draft or draft.get("done"):
            return False, "There is no pick in progress.", draft
        if draft["turn"] != team:
            return False, f"It is {draft['turn']}'s turn to pick.", draft
        chosen = next((p for p in draft["pool"] if p["id"] == player_id), None)
        if not chosen:
            return False, "That player is no longer available.", draft

        draft["picks"].append({"seq": len(draft["picks"]) + 1, "id": chosen["id"], "name": chosen["name"], "team": team, "auto": auto})
        draft["pool"] = [p for p in draft["pool"] if p["id"] != player_id]
        black = sum(1 for p in draft["picks"] if p["team"] == "Black")
        white = sum(1 for p in draft["picks"] if p["team"] == "White")
        turn = draft_next_turn(black, white, draft["per_side"], team)
        if turn is None or not draft["pool"]:
            draft["done"], draft["turn"], draft["deadline"] = True, None, None
        else:
            pick_seconds = (await self.get_config())["pick_seconds"]
            draft["turn"], draft["deadline"] = turn, self.clock() + pick_seconds
        await self._save_draft(draft)
        return True, "ok", draft

    async def expire_pick(self) -> Optional[dict]:
        """Auto-pick a random available player when the captain ran out of time. Returns the pick or None."""
        async with self._draft_lock:
            session = await self.get_session() or {}
            draft = session.get("draft")
            if not draft or draft.get("done") or not draft.get("deadline") or self.clock() < draft["deadline"]:
                return None
            if not draft["pool"]:
                return None
            chosen = random.choice(draft["pool"])
            ok, _, updated = await self._apply_pick(chosen["id"], draft["turn"], auto=True)
            return updated["picks"][-1] if ok and updated else None

    async def discord_id_for_roblox(self, roblox_id: int) -> Optional[int]:
        doc = await self.db.roblox_oauth_links.find_one({"roblox_id": {"$in": [roblox_id, str(roblox_id)]}})
        try:
            return int(doc["_id"]) if doc else None
        except (TypeError, ValueError):
            return None
