from datetime import datetime, timedelta, timezone

import pytest

from core.services.scrim_bridge import drain_inbox


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction):
        self.docs = sorted(self.docs, key=lambda d: d[key], reverse=direction < 0)
        return self

    async def to_list(self, length=None):
        return self.docs[:length]


class Collection:
    def __init__(self):
        self.docs = {}

    def find(self, flt):
        return Cursor([dict(d) for d in self.docs.values()])

    async def find_one_and_delete(self, flt):
        return self.docs.pop(flt["_id"], None)

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)


class Db:
    def __init__(self):
        self.scrim_inbox = Collection()
        self.scrim_outbox = Collection()


def park(db, request_id, payload, age=0, naive=False):
    created = datetime.now(timezone.utc) - timedelta(seconds=age)
    stamp = created.replace(tzinfo=None) if naive else created
    db.scrim_inbox.docs[request_id] = {"_id": request_id, "payload": payload, "created_at": stamp}


@pytest.mark.asyncio
async def test_answers_requests_oldest_first_and_clears_inbox():
    db, order = Db(), []
    park(db, "b", {"n": 2}, age=1)
    park(db, "a", {"n": 1}, age=3)

    async def handler(payload):
        order.append(payload["n"])
        return {"echo": payload["n"]}

    assert await drain_inbox(db, handler) == 2
    assert order == [1, 2]
    assert db.scrim_inbox.docs == {}
    assert db.scrim_outbox.docs["a"]["response"] == {"echo": 1}


@pytest.mark.asyncio
async def test_stale_requests_are_dropped_not_processed():
    db = Db()
    park(db, "old", {"n": 1}, age=60)

    async def handler(payload):
        raise AssertionError("must not run")

    assert await drain_inbox(db, handler) == 0
    assert db.scrim_inbox.docs == {} and db.scrim_outbox.docs == {}


@pytest.mark.asyncio
async def test_naive_utc_timestamps_from_pymongo_are_handled():
    db = Db()
    park(db, "x", {"n": 1}, age=1, naive=True)

    async def handler(payload):
        return {"ok": True}

    assert await drain_inbox(db, handler) == 1


@pytest.mark.asyncio
async def test_handler_failure_still_answers_with_an_error():
    db = Db()
    park(db, "x", {})

    async def handler(payload):
        raise RuntimeError("boom")

    assert await drain_inbox(db, handler) == 1
    assert set(db.scrim_outbox.docs["x"]["response"]) == {"error"}


@pytest.mark.asyncio
async def test_request_claimed_elsewhere_is_skipped():
    db = Db()
    park(db, "x", {})
    original = db.scrim_inbox.find_one_and_delete

    async def already_gone(flt):
        await original(flt)  # someone else took it
        return None

    db.scrim_inbox.find_one_and_delete = already_gone

    async def handler(payload):
        raise AssertionError("must not run")

    assert await drain_inbox(db, handler) == 0
