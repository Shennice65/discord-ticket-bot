import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from main import BotCommandTree


class CommandBlockingTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_user_is_rejected_with_message(self):
        tree = object.__new__(BotCommandTree)
        tree.client = SimpleNamespace(
            db=SimpleNamespace(get_setting=AsyncMock(return_value=[123]))
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        allowed = await tree.interaction_check(interaction)

        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once_with(
            "next time dont mess with the big boss fool",
            ephemeral=True,
        )

    async def test_unblocked_user_is_allowed(self):
        tree = object.__new__(BotCommandTree)
        tree.client = SimpleNamespace(
            db=SimpleNamespace(get_setting=AsyncMock(return_value=[456]))
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        allowed = await tree.interaction_check(interaction)

        self.assertTrue(allowed)
        interaction.response.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
