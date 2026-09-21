import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from framework.approvals import ApprovalManager
from framework.pipeline import ToolExecutionPipeline
from framework.plugins import PluginRegistry


class AgentArchitectureTests(unittest.IsolatedAsyncioTestCase):
    def test_casual_text_does_not_offer_tools(self):
        from ai.router import AIRouter

        self.assertFalse(AIRouter._should_offer_tools("hey what is up"))
        self.assertTrue(AIRouter._should_offer_tools("what is my current rank?"))

    def test_tool_player_references_become_discord_mentions(self):
        from ai.router import AIRouter

        text = AIRouter._apply_tool_mentions("vink is top, Discord user 7 is next", [{
            "user_id": 7, "player_name": "vink", "player_mention": "<@7>",
        }])
        self.assertEqual("<@7> is top, <@7> is next", text)

    async def test_pipeline_rejects_unknown_and_approval_required_tools(self):
        registry = SimpleNamespace(definitions=[{
            "function": {"name": "read_tool"},
        }], execute=AsyncMock(return_value={"ok": True}))
        bot = SimpleNamespace()
        pipeline = ToolExecutionPipeline(
            registry, bot, approvals=ApprovalManager(["read_tool"])
        )

        rejected = await pipeline.execute("read_tool", {}, SimpleNamespace(), SimpleNamespace())
        unknown = await pipeline.execute("not_enabled", {}, SimpleNamespace(), SimpleNamespace())

        self.assertEqual("approval_required", rejected["error"])
        self.assertEqual("tool is not enabled", unknown["error"])
        registry.execute.assert_not_awaited()

    async def test_pipeline_wraps_and_bounds_tool_output(self):
        registry = SimpleNamespace(definitions=[{
            "function": {"name": "read_tool"},
        }], execute=AsyncMock(return_value={"payload": "x" * 3000}))
        pipeline = ToolExecutionPipeline(registry, SimpleNamespace())

        result = await pipeline.execute("read_tool", {}, SimpleNamespace(), SimpleNamespace())

        self.assertEqual("ok", result["status"])
        self.assertEqual(1500, len(result["data"]["payload"]))

    async def test_plugin_registry_keeps_mutating_tools_out_of_read_only_definitions(self):
        plugins = PluginRegistry()
        plugins.register_tool("safe", AsyncMock(), read_only=True)
        plugins.register_tool("dangerous", AsyncMock(), read_only=False, risk="high")
        bot = SimpleNamespace(plugin_registry=plugins, db=SimpleNamespace())

        from ai.tools import ReadOnlyToolRegistry
        registry = ReadOnlyToolRegistry(bot, SimpleNamespace())
        names = {item["function"]["name"] for item in registry.definitions}

        self.assertIn("safe", names)
        self.assertNotIn("dangerous", names)


if __name__ == "__main__":
    unittest.main()
