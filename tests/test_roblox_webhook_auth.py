import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from web.dashboard import Dashboard


def make_request(headers=None, body=None):
    return SimpleNamespace(
        headers=headers or {},
        json=AsyncMock(return_value=body or {"stats": []}),
    )


class RobloxWebhookAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = SimpleNamespace(
            process_new_match=AsyncMock(),
            process_live_update=AsyncMock(),
        )
        self.bot = SimpleNamespace(get_cog=MagicMock(return_value=self.cog))
        self.dashboard = Dashboard(self.bot)

    async def assert_rejected(self, handler, request):
        response = await handler(request)

        self.assertEqual(response.status, 401)
        self.assertEqual(json.loads(response.text), {"error": "Unauthorized"})
        request.json.assert_not_awaited()
        self.cog.process_new_match.assert_not_awaited()
        self.cog.process_live_update.assert_not_awaited()

    async def test_missing_header_is_rejected(self):
        with patch("web.dashboard.Config.ROBLOX_WEBHOOK_SECRET", "s3cret"):
            for handler in (self.dashboard.post_roblox_match, self.dashboard.post_roblox_live):
                await self.assert_rejected(handler, make_request())

    async def test_wrong_secret_is_rejected(self):
        with patch("web.dashboard.Config.ROBLOX_WEBHOOK_SECRET", "s3cret"):
            for handler in (self.dashboard.post_roblox_match, self.dashboard.post_roblox_live):
                await self.assert_rejected(
                    handler, make_request({"X-Roblox-Secret": "wrong"})
                )

    async def test_non_ascii_secret_is_rejected_without_error(self):
        with patch("web.dashboard.Config.ROBLOX_WEBHOOK_SECRET", "s3cret"):
            await self.assert_rejected(
                self.dashboard.post_roblox_match,
                make_request({"X-Roblox-Secret": "s3crét"}),
            )

    async def test_unset_secret_rejects_everything(self):
        with patch("web.dashboard.Config.ROBLOX_WEBHOOK_SECRET", ""):
            for handler in (self.dashboard.post_roblox_match, self.dashboard.post_roblox_live):
                await self.assert_rejected(handler, make_request())
                await self.assert_rejected(
                    handler, make_request({"X-Roblox-Secret": ""})
                )

    async def test_correct_secret_processes_match(self):
        body = {"stats": [{"PlayerName": "a", "Kills": 1}]}
        with patch("web.dashboard.Config.ROBLOX_WEBHOOK_SECRET", "s3cret"):
            response = await self.dashboard.post_roblox_match(
                make_request({"X-Roblox-Secret": "s3cret"}, body)
            )

        self.assertEqual(response.status, 200)
        self.cog.process_new_match.assert_awaited_once_with(body)

    async def test_correct_secret_processes_live_update(self):
        body = {"stats": [], "killfeed_message": "a killed b"}
        with patch("web.dashboard.Config.ROBLOX_WEBHOOK_SECRET", "s3cret"):
            response = await self.dashboard.post_roblox_live(
                make_request({"X-Roblox-Secret": "s3cret"}, body)
            )

        self.assertEqual(response.status, 200)
        self.cog.process_live_update.assert_awaited_once_with(body)


if __name__ == "__main__":
    unittest.main()
