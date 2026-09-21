import sys
import types as pytypes
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# One legacy test installs a tiny aiohttp stub before importing discord.py.
# Restore the installed package so the chat cog can be imported normally.
if not hasattr(sys.modules.get("aiohttp"), "ClientWebSocketResponse"):
    sys.modules.pop("aiohttp", None)
    import aiohttp  # noqa: F401


# The repository's test environment does not need a live Gemini SDK for these
# deterministic context tests. Provide the import surface used by cogs.chat.
google_module = pytypes.ModuleType("google")
genai_module = pytypes.ModuleType("google.genai")
genai_module.Client = object
types_module = pytypes.ModuleType("google.genai.types")
types_module.EmbedContentConfig = lambda **kwargs: kwargs
google_module.genai = genai_module
genai_module.types = types_module
sys.modules.setdefault("google", google_module)
sys.modules.setdefault("google.genai", genai_module)
sys.modules.setdefault("google.genai.types", types_module)

from cogs.chat import Chat
from config import Config
from core.services.server_brain import ServerBrain, ConversationExchange, IdentityCorrection
from core.services import chat_prompts
from core.services.memory_extractor import MemoryExtractor
import asyncio


class ChatContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chat = object.__new__(Chat)

    def message(self, channel_id=123, guild_id=456, content="hello there", author_id=789):
        author = SimpleNamespace(id=author_id, bot=False, display_name="member")
        channel = SimpleNamespace(id=channel_id)
        return SimpleNamespace(
            id=999,
            author=author,
            channel=channel,
            guild=SimpleNamespace(id=guild_id),
            content=content,
            created_at=datetime.now(timezone.utc),
            reference=None,
            reactions=[],
        )

    def test_learning_is_scoped_to_configured_channel(self):
        message = self.message(channel_id=222)
        with patch.object(Config, "AI_MEMORY_CHANNEL_ID", 123):
            self.assertFalse(self.chat._is_memory_channel(message))
        with patch.object(Config, "AI_MEMORY_CHANNEL_ID", 222):
            self.assertTrue(self.chat._is_memory_channel(message))

    async def test_memory_channel_prefers_mongodb_config(self):
        config_collection = SimpleNamespace(
            find_one=AsyncMock(return_value={"AI_MEMORY_CHANNEL_ID": "321"})
        )
        self.chat.bot = SimpleNamespace(
            db=SimpleNamespace(db=SimpleNamespace(config=config_collection))
        )
        with patch.object(Config, "AI_MEMORY_CHANNEL_ID", 123):
            channel_id = await self.chat._refresh_memory_channel_id()
        self.assertEqual(321, channel_id)
        self.assertEqual(321, self.chat.memory_channel_id)

    def test_direct_question_requires_explicit_bot_address(self):
        message = self.message(content="what happened today?")
        self.chat.bot = SimpleNamespace(user=SimpleNamespace(display_name="Atlas", name="Atlas"))
        self.assertFalse(self.chat._is_direct_question(message))
        message.content = "atlas what happened today?"
        self.assertTrue(self.chat._is_direct_question(message))

    async def test_raw_message_is_queued_without_database_io(self):
        message = self.message(content="this is useful context")
        chat_messages = SimpleNamespace(update_one=AsyncMock())
        pending_lore = SimpleNamespace(update_one=AsyncMock())
        self.chat.bot = SimpleNamespace(
            db=SimpleNamespace(chat_messages=chat_messages, db=SimpleNamespace(pending_lore=pending_lore))
        )
        self.chat.evidence_queue = asyncio.Queue()
        with patch.object(Config, "AI_MEMORY_CHANNEL_ID", 123):
            await self.chat._record_message_evidence(message)
        chat_messages.update_one.assert_not_awaited()
        pending_lore.update_one.assert_not_awaited()
        self.assertEqual(message.id, (await self.chat.evidence_queue.get())["message_id"])

    async def test_evidence_worker_persists_queued_message(self):
        message = self.message(content="this is useful context")
        chat_messages = SimpleNamespace(update_one=AsyncMock())
        pending_lore = SimpleNamespace(update_one=AsyncMock())
        self.chat.bot = SimpleNamespace(db=SimpleNamespace(chat_messages=chat_messages, pending_lore=pending_lore))
        self.chat.evidence_queue = asyncio.Queue()
        await self.chat.evidence_queue.put({"message_id": message.id, "guild_id": 456,
                                            "channel_id": 123, "content": message.content,
                                            "user_text": "[member] this is useful context"})
        await Chat.persist_evidence_queue.coro(self.chat)
        chat_messages.update_one.assert_awaited_once()
        pending_lore.update_one.assert_awaited_once()

    async def test_evidence_worker_requeues_failed_batch(self):
        chat_messages = SimpleNamespace(update_one=AsyncMock(side_effect=RuntimeError("db down")))
        self.chat.bot = SimpleNamespace(db=SimpleNamespace(chat_messages=chat_messages))
        self.chat.evidence_queue = asyncio.Queue(maxsize=3)
        evidence = {"message_id": 1, "guild_id": 2, "channel_id": 3, "content": "text"}
        await self.chat.evidence_queue.put(evidence)
        await Chat.persist_evidence_queue.coro(self.chat)
        self.assertEqual(1, self.chat.evidence_queue.qsize())

    async def test_off_channel_message_is_not_stored(self):
        message = self.message(channel_id=222, content="should stay out of memory")
        chat_messages = SimpleNamespace(update_one=AsyncMock())
        pending_lore = SimpleNamespace(update_one=AsyncMock())
        self.chat.bot = SimpleNamespace(
            db=SimpleNamespace(chat_messages=chat_messages, db=SimpleNamespace(pending_lore=pending_lore))
        )
        with patch.object(Config, "AI_MEMORY_CHANNEL_ID", 123):
            await self.chat._record_message_evidence(message)
        chat_messages.update_one.assert_not_awaited()
        pending_lore.update_one.assert_not_awaited()

    async def test_lore_queue_deletes_only_after_persisting(self):
        pending_id = object()
        pending = {
            "_id": pending_id,
            "message_id": 111,
            "guild_id": 456,
            "channel_id": 123,
            "user_text": "useful message here",
        }

        class Cursor:
            def limit(self, _amount):
                return self

            async def to_list(self, length):
                return [pending]

        pending_lore = SimpleNamespace(find=lambda _query: Cursor(), delete_many=AsyncMock())
        memory = SimpleNamespace(update_one=AsyncMock())
        self.chat.client = object()
        self.chat.bot = SimpleNamespace(
            db=SimpleNamespace(db=SimpleNamespace(pending_lore=pending_lore), chat_memory=memory)
        )
        self.chat._api_call_with_fallback = AsyncMock(
            return_value=SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2])])
        )
        self.chat.memory_extractor = SimpleNamespace(process=AsyncMock(return_value=(True, 1)))

        with patch.object(Config, "AI_MEMORY_CHANNEL_ID", 123):
            await Chat.process_lore_queue.coro(self.chat)

        pending_lore.delete_many.assert_awaited_once_with({"_id": {"$in": [pending_id]}})

    async def test_lore_queue_keeps_item_when_embedding_fails(self):
        pending = {
            "_id": object(), "message_id": 111, "guild_id": 456,
            "channel_id": 123, "user_text": "useful message here",
        }

        class Cursor:
            def limit(self, _amount):
                return self

            async def to_list(self, length):
                return [pending]

        pending_lore = SimpleNamespace(find=lambda _query: Cursor(), delete_many=AsyncMock())
        self.chat.client = object()
        self.chat.bot = SimpleNamespace(
            db=SimpleNamespace(
                db=SimpleNamespace(pending_lore=pending_lore),
                chat_memory=SimpleNamespace(update_one=AsyncMock()),
            )
        )
        self.chat._api_call_with_fallback = AsyncMock(side_effect=RuntimeError("quota"))
        self.chat.memory_extractor = SimpleNamespace(process=AsyncMock(return_value=(False, 0)))

        with patch.object(Config, "AI_MEMORY_CHANNEL_ID", 123):
            await Chat.process_lore_queue.coro(self.chat)

        pending_lore.delete_many.assert_not_awaited()

    async def test_memory_extractor_keeps_single_claim_uncertain_and_merges_sources(self):
        api_call = AsyncMock(return_value=SimpleNamespace(text='{"memories":[{"type":"inside_joke","name":"bot curse","summary":"BOT22 predictions cause the opposite result","associated_users":["BOT22"],"source_message_ids":[1],"confidence":0.9,"importance":0.7}]}'))
        extractor = MemoryExtractor(api_call)
        existing = {"confidence": 0.4, "source_message_ids": [2], "first_seen": datetime.now(timezone.utc), "last_seen": datetime.now(timezone.utc)}
        memory = SimpleNamespace(find_one=AsyncMock(return_value=existing), update_one=AsyncMock())
        db = SimpleNamespace(chat_memory=memory)
        records = [{"message_id": 1, "guild_id": 10, "channel_id": 20, "user_text": "bot curse", "timestamp": datetime.now(timezone.utc)}]
        success, stored = await extractor.process(db, records)
        self.assertTrue(success)
        self.assertEqual(1, stored)
        update = memory.update_one.await_args.args[1]["$set"]
        self.assertEqual(0.55, update["confidence"])
        self.assertEqual([2, 1], update["source_message_ids"])

    async def test_memory_extractor_ignores_bot_evidence(self):
        extractor = MemoryExtractor(AsyncMock())
        db = SimpleNamespace(chat_memory=SimpleNamespace(update_one=AsyncMock()))
        success, stored = await extractor.process(db, [{"message_id": 1, "author_bot": True}])
        self.assertTrue(success)
        self.assertEqual(0, stored)
        extractor.api_call.assert_not_awaited()

    async def test_server_brain_groups_reply_chain_and_same_channel_recent_messages(self):
        now = datetime.now(timezone.utc)
        bot_user = SimpleNamespace(id=1, display_name="Atlas")
        db = SimpleNamespace(get_player_rank=AsyncMock(return_value="Gold"),
                             get_chat_context_memories=AsyncMock(return_value=[]))
        bot = SimpleNamespace(user=bot_user, db=db)
        author = SimpleNamespace(id=7, bot=False, display_name="member", roles=[],
                                 guild_permissions=SimpleNamespace(administrator=False))
        guild = SimpleNamespace(id=10, name="Guild", members=[])
        parent = SimpleNamespace(id=40, author=author, channel=None, guild=guild,
                                 content="parent question", created_at=now - timedelta(minutes=2),
                                 reference=None, mentions=[], attachments=[])
        channel = SimpleNamespace(id=20, name="general")
        parent.channel = channel
        recent = SimpleNamespace(id=41, author=author, channel=channel, guild=guild,
                                 content="same thread follow-up", created_at=now - timedelta(minutes=1),
                                 reference=SimpleNamespace(message_id=40, channel_id=20), mentions=[], attachments=[])
        current = SimpleNamespace(id=42, author=author, channel=channel, guild=guild,
                                  content="what is my rank?", created_at=now,
                                  reference=SimpleNamespace(message_id=41, channel_id=20), mentions=[], attachments=[])

        current.reference.resolved = recent
        channel.history = AsyncMock(side_effect=AssertionError("history should not be fetched"))
        channel.fetch_message = AsyncMock(side_effect=AssertionError("reply parent should be cached"))
        brain = ServerBrain(bot, AsyncMock(return_value=[0.1]))
        brain.observe(parent)
        brain.observe(recent)
        context = await brain.build_context(current)
        self.assertEqual([41, 40], [item.message_id for item in context.reply_chain])
        self.assertFalse(any(item.message_id == 1 for item in context.recent_messages))
        self.assertEqual("Gold", context.verified_rank.rank)
        db.get_chat_context_memories.assert_not_awaited()
        brain.embed_query.assert_not_awaited()
        self.assertEqual(channel.history.await_count, 0)
        self.assertEqual(channel.fetch_message.await_count, 0)

    async def test_server_brain_excludes_other_channel_context(self):
        bot = SimpleNamespace(user=SimpleNamespace(id=1), db=SimpleNamespace(
            get_player_rank=AsyncMock(return_value="Gold"), get_chat_context_memories=AsyncMock(return_value=[])))
        brain = ServerBrain(bot, AsyncMock())
        now = datetime.now(timezone.utc)
        def make(mid, channel_id, text):
            return SimpleNamespace(id=mid, author=SimpleNamespace(id=7, bot=False, display_name="member",
                roles=[], guild_permissions=SimpleNamespace(administrator=False)), channel=SimpleNamespace(id=channel_id, name="x"),
                guild=SimpleNamespace(id=10, name="Guild", members=[]), content=text, created_at=now,
                reference=None, mentions=[], attachments=[])
        brain.observe(make(1, 99, "other channel"))
        current = make(2, 20, "hello")
        current.channel.history = lambda limit, before: _empty_async_generator()
        context = await brain.build_context(current)
        self.assertFalse(context.recent_messages)

    async def test_live_window_includes_immediate_preceding_author_and_recent_messages(self):
        now = datetime.now(timezone.utc)
        bot = SimpleNamespace(user=SimpleNamespace(id=1), db=SimpleNamespace(
            get_player_rank=AsyncMock(return_value=None)))
        brain = ServerBrain(bot, AsyncMock())
        guild = SimpleNamespace(id=10, name="Guild", members=[])
        channel = SimpleNamespace(id=20, name="general")

        def make(mid, author_id, text, created_at, channel_obj=channel, guild_obj=guild):
            return SimpleNamespace(
                id=mid,
                author=SimpleNamespace(id=author_id, bot=False, display_name=f"user-{author_id}",
                                       roles=[], guild_permissions=SimpleNamespace(administrator=False)),
                channel=channel_obj,
                guild=guild_obj,
                content=text,
                created_at=created_at,
                reference=None,
                mentions=[],
                attachments=[],
            )

        preceding = make(10, 44, "Joel said bro vink", now - timedelta(seconds=4))
        nearby = make(11, 55, "another nearby message", now - timedelta(seconds=2))
        old = make(12, 66, "old channel traffic", now - timedelta(minutes=3))
        other_channel = make(13, 77, "different channel", now - timedelta(seconds=1),
                             SimpleNamespace(id=21, name="other"))
        for item in (preceding, nearby, old, other_channel):
            brain.observe(item)

        current = make(14, 88, "who is this person above me?", now)
        context = await brain.build_context(current)

        self.assertEqual([10, 11], [item.message_id for item in context.surrounding_messages])
        self.assertEqual(55, context.surrounding_messages[-1].author_id)
        self.assertNotIn(12, [item.message_id for item in context.surrounding_messages])
        self.assertNotIn(13, [item.message_id for item in context.surrounding_messages])

    async def test_live_window_excludes_reply_chain_duplicates_but_keeps_chain_priority(self):
        now = datetime.now(timezone.utc)
        author = SimpleNamespace(id=7, bot=False, display_name="member", roles=[],
                                 guild_permissions=SimpleNamespace(administrator=False))
        guild = SimpleNamespace(id=10, name="Guild", members=[])
        channel = SimpleNamespace(id=20, name="general")
        parent = SimpleNamespace(id=30, author=author, channel=channel, guild=guild,
                                 content="parent", created_at=now - timedelta(seconds=10),
                                 reference=None, mentions=[], attachments=[])
        current = SimpleNamespace(id=31, author=author, channel=channel, guild=guild,
                                  content="reply", created_at=now,
                                  reference=SimpleNamespace(message_id=30, channel_id=20),
                                  mentions=[], attachments=[])
        current.reference.resolved = parent
        bot = SimpleNamespace(user=SimpleNamespace(id=1), db=SimpleNamespace(
            get_player_rank=AsyncMock(return_value=None)))
        brain = ServerBrain(bot, AsyncMock())
        brain.observe(parent)
        context = await brain.build_context(current)
        self.assertEqual([30], [item.message_id for item in context.reply_chain])
        self.assertNotIn(30, [item.message_id for item in context.surrounding_messages])

    async def test_high_confidence_memory_is_served_from_local_cache(self):
        now = datetime.now(timezone.utc)
        record = {"guild_id": 10, "channel_id": 20, "record_type": "inside_joke",
                  "memory_key": "bot curse", "summary": "BOT22 predictions reverse results",
                  "associated_users": ["BOT22"], "confidence": 0.8, "importance": 0.7}
        loader = AsyncMock(return_value=[record])
        bot = SimpleNamespace(user=SimpleNamespace(id=1), db=SimpleNamespace(
            load_chat_memory_cache=loader, get_player_rank=AsyncMock(return_value=None)))
        brain = ServerBrain(bot, AsyncMock())
        await brain.refresh_memory_cache(20)
        author = SimpleNamespace(id=7, bot=False, display_name="member", roles=[],
                                 guild_permissions=SimpleNamespace(administrator=False))
        channel = SimpleNamespace(id=30, name="chat")
        message = SimpleNamespace(id=2, author=author, channel=channel,
                                  guild=SimpleNamespace(id=10, name="Guild", members=[]),
                                  content="what about the bot curse?", created_at=now,
                                  reference=None, mentions=[], attachments=[])
        context = await brain.build_context(message)
        self.assertEqual("bot curse", context.memories[0]["memory_key"])
        loader.assert_awaited_once_with(20, minimum_confidence=0.5, limit=250)

    def test_prompt_labels_community_memories_as_uncertain(self):
        context = SimpleNamespace(current=SimpleNamespace(author_id=7, author_name="member", content="hello",
            guild_id=10, channel_id=20),
            server_name="Guild", channel_name="general", author_roles=(), author_is_admin=False, admins=(),
            reply_chain=(), surrounding_messages=(), recent_messages=(), exchanges=(), verified_rank=None,
            memories=[{"summary": "community claim", "confidence": 0.4, "source_message_ids": [1]}])
        rendered = chat_prompts.context_text(context)
        self.assertIn("uncertain_community_memories", rendered)
        self.assertIn("community claim", rendered)

    def test_prompt_labels_live_speakers_and_mentions(self):
        item = SimpleNamespace(message_id=9, author_id=44, author_name="Joel", content="bro vink",
                               reply_to=None, mentioned_users=(55,), created_at=datetime.now(timezone.utc))
        context = SimpleNamespace(current=SimpleNamespace(author_id=7, author_name="member", content="who is above",
            guild_id=10, channel_id=20), server_name="Guild", channel_name="general", author_roles=(),
            author_is_admin=False, admins=(), reply_chain=(), surrounding_messages=(item,), recent_messages=(),
            exchanges=(), verified_rank=None, memories=[])
        rendered = chat_prompts.context_text(context)
        self.assertIn("message_immediately_before_current", rendered)
        self.assertIn("recent_channel_messages", rendered)
        self.assertIn("Joel", rendered)
        self.assertIn("mentioned_user_ids", rendered)

    def test_prompt_marks_bot_claims_and_identity_corrections(self):
        bot_claim = SimpleNamespace(message_id=9, author_id=1, author_name="Atlas",
                                    content="Polos is CherryBomb", reply_to=7,
                                    mentioned_users=(), mentioned_user_names=(), is_bot=True,
                                    created_at=datetime.now(timezone.utc))
        context = SimpleNamespace(
            current=SimpleNamespace(message_id=10, author_id=44, author_name="Polos",
                content="I'm not even CherryBomb", guild_id=10, channel_id=20,
                reply_to=9, mentioned_users=(), mentioned_user_names=(), is_bot=False,
                created_at=datetime.now(timezone.utc)),
            server_name="Guild", channel_name="general", author_roles=(), author_is_admin=False,
            admins=(), reply_chain=(bot_claim,), surrounding_messages=(), recent_messages=(),
            exchanges=(), verified_rank=None, memories=[],
            identity_correction=IdentityCorrection("I'm not even CherryBomb", "CherryBomb"),
        )
        rendered = chat_prompts.context_text(context)
        self.assertIn('"speaker_type": "bot"', rendered)
        self.assertIn('"rejected_label": "CherryBomb"', rendered)
        self.assertIn('"author_id": 44', rendered)

    async def test_exchange_keeps_discord_author_identity(self):
        now = datetime.now(timezone.utc)
        author = SimpleNamespace(id=44, bot=False, display_name="Polos", roles=[],
                                 guild_permissions=SimpleNamespace(administrator=False))
        guild = SimpleNamespace(id=10, name="Guild", members=[])
        channel = SimpleNamespace(id=20, name="general")
        previous = SimpleNamespace(id=7, author=author, channel=channel, guild=guild,
                                   content="LOL", created_at=now - timedelta(seconds=5),
                                   reference=None, mentions=[], attachments=[])
        current = SimpleNamespace(id=8, author=author, channel=channel, guild=guild,
                                  content="who are you talking about?", created_at=now,
                                  reference=None, mentions=[], attachments=[])
        bot = SimpleNamespace(user=SimpleNamespace(id=1), db=SimpleNamespace(
            get_player_rank=AsyncMock(return_value=None)))
        brain = ServerBrain(bot, AsyncMock())
        brain.observe(previous)
        brain.remember_exchange(previous, "LOL", "Polos is definitely CherryBomb")
        context = await brain.build_context(current)
        self.assertEqual(1, len(context.exchanges))
        exchange = context.exchanges[0]
        self.assertIsInstance(exchange, ConversationExchange)
        self.assertEqual(44, exchange.author_id)
        self.assertEqual("Polos", exchange.author_name)
        self.assertEqual("Polos is definitely CherryBomb", exchange.bot_text)

    def test_identity_correction_detection(self):
        correction = ServerBrain.detect_identity_correction("I'm not even CherryBomb")
        self.assertEqual("CherryBomb", correction.rejected_label)
        self.assertIsNone(ServerBrain.detect_identity_correction("I like CherryBomb"))

    def test_cached_exchange_turns_are_identity_labeled(self):
        exchange = ConversationExchange(7, 10, 20, 44, "Polos", "LOL", "bot reply", datetime.now(timezone.utc))
        user_turn, bot_turn = chat_prompts.labeled_exchange(exchange)
        self.assertIn("DISCORD_USER id=44 name=Polos", user_turn)
        self.assertIn("BOT_RESPONSE to_user_id=44", bot_turn)


async def _empty_async_generator():
    if False:
        yield None


if __name__ == "__main__":
    unittest.main()
