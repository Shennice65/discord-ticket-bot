import asyncio
import json
import sys
import types as pytypes
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

if not hasattr(sys.modules.get("aiohttp"), "ClientWebSocketResponse"):
    sys.modules.pop("aiohttp", None)
    import aiohttp  # noqa: F401


# Keep tests independent from a live Gemini SDK.
google_module = pytypes.ModuleType("google")
genai_module = pytypes.ModuleType("google.genai")
types_module = pytypes.ModuleType("google.genai.types")
errors_module = pytypes.ModuleType("google.genai.errors")
genai_module.Client = lambda **kwargs: object()
types_module.HttpOptions = lambda **kwargs: kwargs
types_module.EmbedContentConfig = lambda **kwargs: kwargs
types_module.GenerateContentConfig = lambda **kwargs: kwargs
types_module.Tool = lambda **kwargs: kwargs
types_module.FunctionDeclaration = lambda **kwargs: kwargs
types_module.AutomaticFunctionCallingConfig = lambda **kwargs: kwargs
types_module.Content = lambda **kwargs: kwargs
types_module.Part = SimpleNamespace(
    from_text=lambda **kwargs: kwargs,
    from_bytes=lambda **kwargs: kwargs,
    from_function_call=lambda **kwargs: kwargs,
    from_function_response=lambda **kwargs: kwargs,
)
types_module.GenerateContentResponse = object
errors_module.APIError = type("APIError", (Exception,), {})
google_module.genai = genai_module
genai_module.types = types_module
genai_module.errors = errors_module
sys.modules.setdefault("google", google_module)
sys.modules.setdefault("google.genai", genai_module)
sys.modules.setdefault("google.genai.types", types_module)
sys.modules.setdefault("google.genai.errors", errors_module)

from ai.llm import GeminiLLM
from ai import prompts
from ai.router import AIRouter
from context.context_builder import ContextBuilder, IdentityCorrection
from context.conversation_tracker import ContextMessage, ConversationExchange, ConversationTracker
from context.retrieval import MemoryRetriever
from cogs.chat import Chat
from memory.extractor import MemoryExtractor


def make_message(message_id=1, channel_id=20, guild_id=10, content="hello", author_id=7,
                 author_name="member", created_at=None, reference=None, mentions=None,
                 bot=False):
    author = SimpleNamespace(
        id=author_id, bot=bot, display_name=author_name, roles=[],
        guild_permissions=SimpleNamespace(administrator=False),
    )
    channel = SimpleNamespace(id=channel_id, name="general", type="text")
    guild = SimpleNamespace(id=guild_id, name="Guild", members=[])
    return SimpleNamespace(
        id=message_id, author=author, channel=channel, guild=guild,
        content=content, created_at=created_at or datetime.now(timezone.utc),
        reference=reference, mentions=mentions or [], attachments=[], reactions=[],
    )


class ChatContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_rank_context_uses_dictionary_shape(self):
        bot = SimpleNamespace(
            user=SimpleNamespace(id=1),
            db=SimpleNamespace(get_player_rank=AsyncMock(return_value="Gold")),
        )
        builder = ContextBuilder(bot, ConversationTracker(bot), MemoryRetriever(bot))
        rank = await builder.get_verified_rank(make_message(content="what is my rank?"))
        self.assertEqual({"user_id": 7, "name": "member", "rank": "Gold"}, rank)

        context = SimpleNamespace(
            current=SimpleNamespace(message_id=1, author_id=7, author_name="member", content="rank", guild_id=10, channel_id=20,
                                    reply_to=None, mentioned_users=(), created_at=datetime.now(timezone.utc)),
            server_name="Guild", channel_name="general", author_roles=(), author_is_admin=False, admins=(),
            reply_chain=(), immediate_preceding=None, surrounding_messages=(), recent_messages=(),
            verified_rank=rank, memories=[], identity_correction=None,
        )
        payload = json.loads(prompts.context_text(context).split("\n", 1)[1])
        self.assertEqual("Gold", payload["verified_rank"]["rank"])

    async def test_immediate_preceding_is_not_live_window_tail(self):
        now = datetime.now(timezone.utc)
        bot = SimpleNamespace(user=SimpleNamespace(id=1), db=SimpleNamespace(get_player_rank=AsyncMock(return_value=None)))
        tracker = ConversationTracker(bot)
        nearby = make_message(8, content="older nearby", author_id=8, author_name="proxic", created_at=now - timedelta(seconds=5))
        parent = make_message(9, content="reply parent", author_id=9, author_name="Polos", created_at=now - timedelta(seconds=1))
        tracker.observe(nearby)
        tracker.observe(parent)
        current = make_message(10, content="who is above me?", author_id=7, created_at=now,
                               reference=SimpleNamespace(message_id=9, channel_id=20, resolved=parent))
        context = await ContextBuilder(bot, tracker, MemoryRetriever(bot)).build(current)
        self.assertEqual(9, context.immediate_preceding.message_id)
        self.assertEqual("Polos", context.immediate_preceding.author_name)

    async def test_memory_retrieval_requires_overlap_and_scope(self):
        retriever = MemoryRetriever(SimpleNamespace(db=None))
        retriever._memory_cache_channel_id = 20
        retriever.memory_cache = {10: [
            {"guild_id": 10, "channel_id": 20, "summary": "CherryBomb uses CB", "memory_key": "cherrybomb",
             "associated_users": ["CherryBomb"], "confidence": 0.8, "importance": 0.8},
            {"guild_id": 10, "channel_id": 99, "summary": "wrong channel", "memory_key": "wrong",
             "associated_users": [], "confidence": 0.9, "importance": 0.9},
        ]}
        current = SimpleNamespace(guild_id=10, content="hello")
        self.assertEqual([], retriever.select_cached_memories(current))
        current.content = "what about CherryBomb?"
        self.assertEqual(["cherrybomb"], [item["memory_key"] for item in retriever.select_cached_memories(current)])
        self.assertEqual([], retriever.select_cached_memories(current, excluded_terms=("CherryBomb",)))

    async def test_memory_retrieval_excludes_low_confidence_and_bot_chain(self):
        retriever = MemoryRetriever(SimpleNamespace(db=None))
        retriever._memory_cache_channel_id = 20
        retriever.memory_cache = {10: [
            {"guild_id": 10, "channel_id": 20, "summary": "CherryBomb uses CB", "memory_key": "cherrybomb",
             "associated_users": ["CherryBomb"], "confidence": 0.4, "importance": 1.0},
        ]}
        bot_claim = SimpleNamespace(content="CherryBomb is Polos", is_bot=True, guild_id=10, channel_id=20)
        current = SimpleNamespace(guild_id=10, channel_id=20, content="hello")
        self.assertEqual([], retriever.select_cached_memories(current, (bot_claim,)))

    async def test_identity_correction_is_scoped_and_expires(self):
        tracker = ConversationTracker(SimpleNamespace(user=SimpleNamespace(id=1)))
        correction = IdentityCorrection("I'm not CherryBomb", "CherryBomb")
        current = ContextMessage(1, 10, 20, 44, "Polos", "I'm not CherryBomb",
                                datetime.now(timezone.utc), None)
        other_user = ContextMessage(2, 10, 20, 55, "proxic", "yo",
                                    datetime.now(timezone.utc), None)
        self.assertEqual(correction, tracker.identity_correction(current, correction))
        self.assertIsNone(tracker.identity_correction(other_user))
        tracker._identity_corrections[(10, 20, 44)] = (0, correction)
        self.assertIsNone(tracker.identity_correction(current))

    async def test_raw_evidence_is_queued_with_bot_flags(self):
        chat = object.__new__(Chat)
        chat.memory_channel_id = 20
        chat.evidence_queue = asyncio.Queue()
        await chat._record_message_evidence(make_message(channel_id=20, content="this is useful context"))
        evidence = await chat.evidence_queue.get()
        self.assertFalse(evidence["author_bot"])
        self.assertFalse(evidence["is_bot"])
        self.assertEqual(7, evidence["author_id"])

    async def test_evidence_worker_persists_and_requeues_failures(self):
        chat = object.__new__(Chat)
        chat.evidence_queue = asyncio.Queue(maxsize=3)
        chat.bot = SimpleNamespace(db=SimpleNamespace(
            chat_messages=SimpleNamespace(update_one=AsyncMock()),
            pending_lore=SimpleNamespace(update_one=AsyncMock()),
        ))
        evidence = {"message_id": 1, "guild_id": 10, "channel_id": 20, "content": "useful context", "user_text": "useful context"}
        await chat.evidence_queue.put(evidence)
        await Chat.persist_evidence_queue.coro(chat)
        chat.bot.db.chat_messages.update_one.assert_awaited_once()
        chat.bot.db.pending_lore.update_one.assert_awaited_once()

        chat.bot.db.chat_messages.update_one = AsyncMock(side_effect=RuntimeError("db down"))
        await chat.evidence_queue.put(evidence)
        await Chat.persist_evidence_queue.coro(chat)
        self.assertEqual(1, chat.evidence_queue.qsize())

    async def test_compressor_upserts_deterministic_summary_before_delete(self):
        source = {
            "_id": "source-1", "guild_id": 10, "channel_id": 20,
            "summary": "Polos is known for the red ball joke", "source_message_ids": [101],
            "associated_users": ["Polos"], "confidence": 0.7, "importance": 0.6,
            "timestamp": datetime.now(timezone.utc) - timedelta(days=8),
        }

        class Cursor:
            def __init__(self, records):
                self.records = records
            def limit(self, _amount):
                return self
            async def to_list(self, length):
                records, self.records = self.records, []
                return records

        remaining = [True]
        def find(_query):
            if remaining[0]:
                remaining[0] = False
                return Cursor([source])
            return Cursor([])
        collection = SimpleNamespace(find=find, update_one=AsyncMock(), delete_many=AsyncMock())
        fake_llm = SimpleNamespace(
            client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
                embed_content=AsyncMock(return_value=SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1])]))))),
            ensure_keys=AsyncMock(),
            generate_content=AsyncMock(return_value=SimpleNamespace(text="Polos red ball joke")),
        )
        chat = object.__new__(Chat)
        chat.bot = SimpleNamespace(db=SimpleNamespace(chat_memory=collection))
        chat._refresh_memory_channel_id = AsyncMock(return_value=20)
        with patch("ai.llm.llm", fake_llm), patch("cogs.chat.asyncio.sleep", AsyncMock()):
            await Chat.lore_compressor.coro(chat)
        collection.update_one.assert_awaited_once()
        self.assertTrue(collection.update_one.await_args.kwargs["upsert"])
        collection.delete_many.assert_awaited_once_with({"_id": {"$in": ["source-1"]}})

    async def test_compressor_keeps_sources_when_embedding_fails(self):
        source = {
            "_id": "source-2", "guild_id": 10, "channel_id": 20,
            "summary": "community event", "source_message_ids": [102],
            "associated_users": [], "confidence": 0.7, "importance": 0.6,
            "timestamp": datetime.now(timezone.utc) - timedelta(days=8),
        }

        class Cursor:
            called = False
            def limit(self, _amount):
                return self
            async def to_list(self, length):
                if Cursor.called:
                    return []
                Cursor.called = True
                return [source]

        collection = SimpleNamespace(find=lambda _query: Cursor(), update_one=AsyncMock(), delete_many=AsyncMock())
        fake_llm = SimpleNamespace(
            client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
                embed_content=AsyncMock(return_value=SimpleNamespace(embeddings=[]))))),
            ensure_keys=AsyncMock(),
            generate_content=AsyncMock(return_value=SimpleNamespace(text="event")),
        )
        chat = object.__new__(Chat)
        chat.bot = SimpleNamespace(db=SimpleNamespace(chat_memory=collection))
        chat._refresh_memory_channel_id = AsyncMock(return_value=20)
        with patch("ai.llm.llm", fake_llm), patch("cogs.chat.asyncio.sleep", AsyncMock()):
            await Chat.lore_compressor.coro(chat)
        collection.update_one.assert_not_awaited()
        collection.delete_many.assert_not_awaited()

    async def test_malformed_extraction_retains_failure_state(self):
        extractor = MemoryExtractor(SimpleNamespace())
        with patch("memory.extractor.llm.generate_content", AsyncMock(return_value=SimpleNamespace(text="not json"))):
            result = await extractor.process(SimpleNamespace(), [{"message_id": 1, "content": "claim"}])
        self.assertEqual((False, 0), result)

    async def test_empty_valid_extraction_is_successful(self):
        extractor = MemoryExtractor(SimpleNamespace())
        with patch("memory.extractor.llm.generate_content", AsyncMock(return_value=SimpleNamespace(text='{"memories": []}'))):
            result = await extractor.process(SimpleNamespace(), [{"message_id": 1, "content": "ordinary chat"}])
        self.assertEqual((True, 0), result)

    def test_identity_context_keeps_users_and_bot_claims_distinct(self):
        bot_message = SimpleNamespace(message_id=9, author_id=1, author_name="Atlas", content="Polos is CherryBomb",
                                      reply_to=7, mentioned_users=(), mentioned_user_names=(), is_bot=True,
                                      created_at=datetime.now(timezone.utc))
        current = SimpleNamespace(message_id=10, author_id=44, author_name="Polos", content="I'm not CherryBomb",
                                  guild_id=10, channel_id=20, reply_to=9, mentioned_users=(), mentioned_user_names=(),
                                  created_at=datetime.now(timezone.utc))
        context = SimpleNamespace(current=current, server_name="Guild", channel_name="general", author_roles=(),
                                  author_is_admin=False, admins=(), reply_chain=(bot_message,), immediate_preceding=bot_message,
                                  surrounding_messages=(), recent_messages=(), verified_rank=None, memories=[],
                                  identity_correction=IdentityCorrection("I'm not CherryBomb", "CherryBomb"))
        rendered = prompts.context_text(context)
        self.assertIn('"author_id": 1', rendered)
        self.assertIn('"author_id": 44', rendered)
        self.assertIn('"speaker_type": "bot"', rendered)
        self.assertIn('"rejected_label": "CherryBomb"', rendered)

    async def test_tracker_keeps_exchange_identity(self):
        tracker = ConversationTracker(SimpleNamespace(user=SimpleNamespace(id=1)))
        message = make_message(author_id=44, author_name="Polos")
        tracker.remember_exchange(message, "LOL", "bot reply")
        exchange = tracker._exchanges[(10, 20, 44)][0]
        self.assertIsInstance(exchange, ConversationExchange)
        self.assertEqual((44, "Polos"), (exchange.author_id, exchange.author_name))

    async def test_gemini_rotates_after_timeout_and_falls_back(self):
        llm = GeminiLLM.__new__(GeminiLLM)
        llm.current_client_index = 0
        first = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(side_effect=asyncio.TimeoutError()))))
        second = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(return_value=SimpleNamespace(text="ok")))))
        llm.clients = [first, second]
        llm.ATTEMPT_TIMEOUT = 0.01
        response = await llm.generate_content("gemini-3.5-flash", "hello")
        self.assertEqual("ok", response.text)
        self.assertEqual(1, llm.current_client_index)

    async def test_image_search_rejects_inaccessible_channel_before_history(self):
        requester = make_message()
        target = SimpleNamespace(
            name="private", permissions_for=lambda _user: SimpleNamespace(view_channel=False),
            history=AsyncMock(side_effect=AssertionError("history must not be read")),
        )
        requester.guild.text_channels = [target]
        router = AIRouter(SimpleNamespace(user=SimpleNamespace(id=1)), SimpleNamespace())
        result = await router._search_channel_for_image(requester, [], "private")
        self.assertEqual("Error: You cannot view that channel.", result)
        target.history.assert_not_called()

    async def test_database_memory_tool_applies_guild_channel_and_confidence_scope(self):
        requester = make_message(guild_id=44)
        cursor = SimpleNamespace(
            sort=lambda *_args: cursor,
            limit=lambda *_args: cursor,
            to_list=AsyncMock(return_value=[]),
        )
        collection = SimpleNamespace(find=lambda query: (self.assertEqual({
            "guild_id": 44, "channel_id": 20, "confidence": {"$gte": 0.5},
            "associated_users": {"$regex": "Polos", "$options": "i"},
        }, query) or cursor))
        builder = SimpleNamespace(retriever=SimpleNamespace(_memory_cache_channel_id=20))
        bot = SimpleNamespace(db=SimpleNamespace(db=SimpleNamespace(chat_memory=collection)))
        router = AIRouter(bot, builder)
        result = await router._search_database_memory(requester, "Polos")
        self.assertIn("No database lore", result)

    def test_cached_memory_avoids_database_tool(self):
        bot = SimpleNamespace(user=SimpleNamespace(id=1))
        builder = SimpleNamespace(retriever=SimpleNamespace(_memory_cache_channel_id=20))
        router = AIRouter(bot, builder)
        message = make_message(content="who is Polos?")
        context = SimpleNamespace(memories=[{"summary": "Polos community fact"}])
        self.assertFalse(router._needs_memory_lookup(message, context))

    async def test_normal_triggered_reply_uses_one_generation_without_key_or_tool_calls(self):
        bot_user = SimpleNamespace(id=1, display_name="Atlas", name="Atlas")
        bot = SimpleNamespace(user=bot_user)
        message = make_message(content="@Atlas hello", mentions=[bot_user])

        class Typing:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_args):
                return False

        message.channel.typing = lambda: Typing()
        message.reply = AsyncMock()
        message.channel.send = AsyncMock()
        tracker = ConversationTracker(bot)
        current = ContextMessage(message.id, 10, 20, 7, "member", "hello",
                                  message.created_at, None)
        context = SimpleNamespace(
            current=current, server_name="Guild", channel_name="general", author_roles=(),
            author_is_admin=False, admins=(), reply_chain=(), immediate_preceding=None,
            surrounding_messages=(), recent_messages=(), exchanges=(), memories=[],
            verified_rank=None, curated_lore="", identity_correction=None,
        )
        builder = SimpleNamespace(
            tracker=tracker, retriever=SimpleNamespace(_memory_cache_channel_id=20),
            build=AsyncMock(return_value=context),
        )
        router = AIRouter(bot, builder)
        fake_llm = SimpleNamespace(
            client=object(),
            generate_content=AsyncMock(return_value=SimpleNamespace(text="hello", function_calls=[])),
        )
        with patch("ai.router.llm", fake_llm):
            await router.handle_message(message, True, None)

        fake_llm.generate_content.assert_awaited_once()
        self.assertNotIn("ensure_keys", fake_llm.__dict__)
        message.reply.assert_awaited_once_with("hello")
        self.assertEqual("hello", tracker._exchanges[(10, 20, 7)][0].bot_text)

    async def test_tool_execution_is_one_round_and_final_generation_has_no_tools(self):
        bot_user = SimpleNamespace(id=1, display_name="Atlas", name="Atlas")
        bot = SimpleNamespace(user=bot_user)
        message = make_message(content="@Atlas who is Polos?", mentions=[bot_user])

        class Typing:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_args):
                return False

        message.channel.typing = lambda: Typing()
        message.reply = AsyncMock()
        message.channel.send = AsyncMock()
        tracker = ConversationTracker(bot)
        current = ContextMessage(message.id, 10, 20, 7, "member", "who is Polos?",
                                  message.created_at, None)
        context = SimpleNamespace(
            current=current, server_name="Guild", channel_name="general", author_roles=(),
            author_is_admin=False, admins=(), reply_chain=(), immediate_preceding=None,
            surrounding_messages=(), recent_messages=(), exchanges=(), memories=[],
            verified_rank=None, curated_lore="", identity_correction=None,
        )
        builder = SimpleNamespace(
            tracker=tracker, retriever=SimpleNamespace(_memory_cache_channel_id=20),
            build=AsyncMock(return_value=context),
        )
        router = AIRouter(bot, builder)
        responses = [
            SimpleNamespace(text="", function_calls=[SimpleNamespace(
                name="search_database_memory", args={"name": "Polos"})]),
            SimpleNamespace(text="Polos is Polos", function_calls=[]),
        ]
        fake_llm = SimpleNamespace(client=object(), generate_content=AsyncMock(side_effect=responses))
        router._search_database_memory = AsyncMock(return_value="Polos community fact")
        with patch("ai.router.llm", fake_llm):
            await router.handle_message(message, True, None)

        self.assertEqual(2, fake_llm.generate_content.await_count)
        first_config = fake_llm.generate_content.await_args_list[0].kwargs["config"]
        final_config = fake_llm.generate_content.await_args_list[1].kwargs["config"]
        self.assertTrue(first_config["tools"])
        self.assertEqual([], final_config["tools"])
        router._search_database_memory.assert_awaited_once_with(message, name="Polos")
        message.reply.assert_awaited_once_with("Polos is Polos")


if __name__ == "__main__":
    unittest.main()
