import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from core.services.ticket_service import TicketService


class TicketServiceRankChangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unrank_closes_and_deletes_open_ranked_tickets(self):
        ticket = {
            "channel_id": 123,
            "user_id": 10,
            "opponent_id": 20,
        }
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[ticket]))
        tickets = SimpleNamespace(find=MagicMock(return_value=cursor))
        db = SimpleNamespace(tickets=tickets, close_ticket=AsyncMock(return_value=True))
        channel = SimpleNamespace(send=AsyncMock(), delete=AsyncMock())
        bot = SimpleNamespace(get_channel=MagicMock(return_value=channel))

        await TicketService(bot, db).check_and_notify_rank_change(10, "Unranked")

        query = tickets.find.call_args.args[0]
        self.assertEqual({"$in": ["open", "pending_accept"]}, query["status"])
        db.close_ticket.assert_awaited_once_with(123, 10)
        channel.send.assert_awaited_once()
        channel.delete.assert_awaited_once_with(reason="Player unranked")

    async def test_normal_rank_change_keeps_ticket_open(self):
        ticket = {
            "channel_id": 123,
            "user_id": 10,
            "opponent_id": 20,
        }
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[ticket]))
        tickets = SimpleNamespace(find=MagicMock(return_value=cursor))
        db = SimpleNamespace(
            tickets=tickets,
            close_ticket=AsyncMock(),
            get_global_rank_index=AsyncMock(side_effect=[1, 2]),
        )
        channel = SimpleNamespace(send=AsyncMock(), delete=AsyncMock())
        bot = SimpleNamespace(get_channel=MagicMock(return_value=channel))

        await TicketService(bot, db).check_and_notify_rank_change(10, "Champions 2")

        query = tickets.find.call_args.args[0]
        self.assertEqual("open", query["status"])
        db.close_ticket.assert_not_awaited()
        channel.send.assert_awaited_once()
        channel.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
