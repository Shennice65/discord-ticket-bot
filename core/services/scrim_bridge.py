"""Bot side of the Mongo bridge: answers scrim syncs that the clips site parked in `scrim_inbox`."""
import traceback
from datetime import datetime, timezone
from typing import Awaitable, Callable

MAX_BATCH = 20
MAX_AGE_SECONDS = 15  # the script gives up after ~6s, so older requests are stale


def _age_seconds(created_at) -> float:
    if created_at is None:
        return 0.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)  # pymongo returns naive UTC
    return (datetime.now(timezone.utc) - created_at).total_seconds()


async def ensure_indexes(db) -> None:
    """Expire leftovers automatically so the two collections never grow."""
    for name in ("scrim_inbox", "scrim_outbox"):
        try:
            await db[name].create_index("created_at", expireAfterSeconds=120)
        except Exception:
            traceback.print_exc()


async def drain_inbox(db, handler: Callable[[dict], Awaitable[dict]]) -> int:
    """Process parked requests oldest-first. Returns how many were answered."""
    docs = await db.scrim_inbox.find({}).sort("created_at", 1).to_list(length=MAX_BATCH)
    answered = 0
    for doc in docs:
        claimed = await db.scrim_inbox.find_one_and_delete({"_id": doc["_id"]})
        if not claimed:
            continue  # another instance (or the relay timing out) got it first
        if _age_seconds(claimed.get("created_at")) > MAX_AGE_SECONDS:
            continue
        try:
            response = await handler(claimed.get("payload") or {})
        except Exception:
            traceback.print_exc()
            response = {"error": "The bot failed to process the sync."}
        await db.scrim_outbox.insert_one(
            {"_id": claimed["_id"], "response": response, "created_at": datetime.now(timezone.utc)}
        )
        answered += 1
    return answered
