import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database.tickets import TicketsMixin
from main import TicketBot


class TicketDatabaseReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_only_targets_open_ticket(self):
        db = TicketsMixin()
        db.tickets = SimpleNamespace(find_one_and_update=AsyncMock(return_value={"id": 7}))

        claimed = await db.claim_ticket_for_processing(123)

        self.assertEqual({"id": 7}, claimed)
        query, update = db.tickets.find_one_and_update.await_args.args
        self.assertEqual({"channel_id": 123, "status": "open"}, query)
        self.assertEqual("processing", update["$set"]["status"])

    async def test_finalize_only_closes_claimed_ticket(self):
        db = TicketsMixin()
        db.tickets = SimpleNamespace(
            update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1))
        )

        finalized = await db.finalize_processed_ticket(123, 456)

        self.assertTrue(finalized)
        query, update = db.tickets.update_one.await_args.args
        self.assertEqual({"channel_id": 123, "status": "processing"}, query)
        self.assertEqual("closed", update["$set"]["status"])
        self.assertEqual(456, update["$set"]["closed_by"])

    async def test_ranked_result_reuses_existing_id(self):
        db = TicketsMixin()
        db.ranked_results = SimpleNamespace(
            find_one=AsyncMock(return_value={"id": 99}),
            replace_one=AsyncMock()
        )
        db._next_id = AsyncMock()

        await db.add_ranked_result(
            7, 8, "observer", "Masters 2", "Masters 1",
            "Masters 1", "Masters 2", 10, "winner"
        )

        db._next_id.assert_not_awaited()
        query, result = db.ranked_results.replace_one.await_args.args
        self.assertEqual({"ticket_id": 7}, query)
        self.assertEqual(99, result["id"])
        self.assertTrue(db.ranked_results.replace_one.await_args.kwargs["upsert"])


class StartupReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_aborts_when_database_init_fails(self):
        bot = SimpleNamespace(db=SimpleNamespace(init=AsyncMock(return_value=False)))

        with self.assertRaisesRegex(RuntimeError, "MongoDB initialization failed"):
            await TicketBot.setup_hook(bot)


if __name__ == "__main__":
    unittest.main()
