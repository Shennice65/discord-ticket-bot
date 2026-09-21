import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ai.llm import GenerationResult, OpenRouterLLM, ToolCall, sanitize_model_text
from ai.router import AIRouter
from scripts.reset_ai_memory import COLLECTIONS, clear_ai_collections


class FakeCollection:
    def __init__(self, count):
        self.count = count
        self.deleted = False

    async def count_documents(self, _query):
        return 0 if self.deleted else self.count

    async def delete_many(self, _query):
        self.deleted = True


class OpenRouterAgentTests(unittest.IsolatedAsyncioTestCase):
    def test_model_text_does_not_leak_thought_markers(self):
        self.assertEqual("answer", sanitize_model_text("answer</thought>"))
        self.assertEqual("answer", sanitize_model_text("answer<thought>hidden</thought>"))
        self.assertEqual("answer", sanitize_model_text("answer</p>"))
        self.assertEqual("first\nsecond", sanitize_model_text("first\n\nsecond"))
        self.assertEqual(
            "finally figured it out",
            sanitize_model_text(
                "BOT_RESPONSE to_user_id=1 guild_id=2 channel_id=3\nfinally figured it out"
            ),
        )

    async def test_generate_normalizes_tool_calls_and_model_fallbacks(self):
        provider = OpenRouterLLM()
        provider.api_keys = ["test-key"]
        provider.model = "primary/model"
        provider.fallback_models = ["fallback/model"]
        provider._request = AsyncMock(return_value={
            "candidates": [{"content": {"parts": [
                {"text": "done"},
                {"functionCall": {"name": "get_server_rules", "args": {}}},
            ]}}],
            "usageMetadata": {"totalTokenCount": 12},
        })

        result = await provider.generate([{"role": "user", "content": "rules"}], tools=[{
            "type": "function",
            "function": {"name": "get_server_rules", "description": "rules", "parameters": {"type": "object"}},
        }])

        self.assertEqual("done", result.text)
        self.assertEqual("primary/model", result.model)
        self.assertEqual((ToolCall("gemini-call-1", "get_server_rules", {}),), result.tool_calls)
        endpoint = provider._request.await_args.args[1]
        payload = provider._request.await_args.args[2]
        self.assertEqual("generateContent", endpoint)
        self.assertIn("functionDeclarations", payload["tools"][0])

    async def test_embed_uses_fixed_vector_dimension(self):
        provider = OpenRouterLLM()
        provider.api_keys = ["test-key"]
        provider._request = AsyncMock(return_value={
            "embedding": {"values": [0.1, 0.2]},
        })

        result = await provider.embed(["fresh memory"])

        self.assertEqual(256, len(result[0]))
        self.assertEqual([0.1, 0.2], result[0][:2])
        self.assertTrue(all(value == 0.0 for value in result[0][2:]))
        self.assertEqual(256, provider._request.await_args.args[2]["outputDimensionality"])

    async def test_agent_executes_read_only_tool_then_finishes(self):
        bot_user = SimpleNamespace(id=1, display_name="Atlas", name="Atlas")
        bot = SimpleNamespace(user=bot_user, db=SimpleNamespace())
        tracker = SimpleNamespace(
            observe=Mock(),
            resolve_reply_parent=AsyncMock(return_value=None),
            remember_exchange=Mock(),
        )
        context = SimpleNamespace(
            current=SimpleNamespace(
                guild_id=None, channel_id=20, author_id=7, author_name="member",
                content="what are the rules?", message_id=1, created_at=datetime.now(timezone.utc),
                mentioned_users=(), mentioned_user_names=(), is_bot=False,
            ),
            server_name="DM", channel_name="Direct Message", author_roles=(),
            author_is_admin=False, admins=(), reply_chain=(), immediate_preceding=None,
            surrounding_messages=(), recent_messages=(), exchanges=(), memories=[],
            verified_rank=None, curated_lore="", identity_correction=None,
        )
        builder = SimpleNamespace(
            tracker=tracker,
            retriever=SimpleNamespace(memory_cache={}),
            build=AsyncMock(return_value=context),
        )
        message = SimpleNamespace(
            id=1, content="what are the rules?", author=SimpleNamespace(
                id=7, bot=False, guild_permissions=SimpleNamespace(administrator=False), roles=[]
            ), mentions=[], guild=None, attachments=[], created_at=datetime.now(timezone.utc),
            channel=SimpleNamespace(typing=lambda: _Typing(), send=AsyncMock()),
            reply=AsyncMock(),
        )
        first = GenerationResult(
            text="", model="primary/model",
            tool_calls=(ToolCall("call-1", "get_server_rules", {}),),
            assistant_message={
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function", "function": {
                        "name": "get_server_rules", "arguments": "{}"
                    }
                }]
            },
        )
        second = GenerationResult(text="use the ticket channels", model="primary/model")
        fake_llm = SimpleNamespace(
            client=object(), ensure_keys=AsyncMock(), generate=AsyncMock(side_effect=[first, second])
        )

        with patch("ai.router.llm", fake_llm):
            await AIRouter(bot, builder).handle_message(message, True, None)

        self.assertEqual(2, fake_llm.generate.await_count)
        message.reply.assert_awaited_once_with("use the ticket channels")

    async def test_reset_targets_only_ai_collections(self):
        database = {name: FakeCollection(index + 1) for index, name in enumerate(COLLECTIONS)}
        preserved = {
            name: FakeCollection(9)
            for name in ("player_ranks", "tickets", "betting", "clips", "config")
        }
        database.update(preserved)

        before, after = await clear_ai_collections(database)

        self.assertEqual({"chat_memory": 1, "chat_messages": 2, "pending_lore": 3}, before)
        self.assertEqual({name: 0 for name in COLLECTIONS}, after)
        self.assertTrue(all(database[name].deleted for name in COLLECTIONS))
        self.assertTrue(all(not collection.deleted for collection in preserved.values()))


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


if __name__ == "__main__":
    unittest.main()
