"""Bounded conversational context for the existing chat cog (no Gemini SDK)."""

import asyncio
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextMessage:
    message_id: int
    guild_id: int | None
    channel_id: int
    author_id: int
    author_name: str
    content: str
    created_at: datetime
    reply_to: int | None
    mentioned_users: tuple[int, ...] = ()
    is_bot: bool = False

    @property
    def scope(self):
        return self.guild_id, self.channel_id

    @classmethod
    def from_message(cls, message):
        reference = getattr(message, "reference", None)
        attachments = getattr(message, "attachments", ())
        return cls(
            message.id, getattr(message.guild, "id", None), message.channel.id,
            message.author.id, message.author.display_name[:100],
            (message.content or ("[Attachment/Image]" if attachments else ""))[:2000],
            message.created_at, getattr(reference, "message_id", None),
            tuple(user.id for user in message.mentions), message.author.bot,
        )


@dataclass(frozen=True)
class VerifiedRank:
    user_id: int
    name: str
    rank: str | None  # None means unavailable, not unranked.


@dataclass
class BrainContext:
    current: ContextMessage
    server_name: str
    channel_name: str
    author_roles: tuple[str, ...]
    author_is_admin: bool
    admins: tuple[str, ...]
    reply_chain: tuple[ContextMessage, ...] = ()  # Immediate parent first.
    recent_messages: tuple[ContextMessage, ...] = ()
    exchanges: tuple[tuple[str, str], ...] = ()
    memories: list[dict] = field(default_factory=list)
    verified_rank: VerifiedRank | None = None
    curated_lore: str = ""
    query_embedding: list[float] | None = None


class ServerBrain:
    """Own context selection; the cog supplies Discord events and an embed adapter."""

    WINDOW = timedelta(minutes=15)
    MAX_CHANNELS = 128
    MAX_RECENT = 75
    MAX_SELECTED = 12
    MAX_REPLY_DEPTH = 3
    LOOKUP_TIMEOUT = 1.0
    EMBEDDING_TIMEOUT = 1.5

    def __init__(self, bot, embed_query, lore_path="lore.txt"):
        self.bot = bot
        self.embed_query = embed_query
        self.lore_path = Path(lore_path)
        self.recent_messages = OrderedDict()
        self._seeded = set()
        self._exchanges = OrderedDict()
        self._embedding_cache = OrderedDict()

    @staticmethod
    def needs_memory(content):
        text = re.sub(r"<@!?\d+>", "", content).strip().casefold()
        text = re.sub(r"[^\w\s]", "", text).strip()
        return text not in {"", "hi", "hey", "hello", "yo", "sup", "thanks", "thank you", "ok", "lol", "stfu"}

    async def _cached_embedding(self, scope, query):
        key = (*scope, query)
        cached = self._embedding_cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            self._embedding_cache.move_to_end(key)
            return cached[1]
        embedding = await asyncio.wait_for(self.embed_query(query), self.EMBEDDING_TIMEOUT)
        if embedding:
            self._embedding_cache[key] = (time.monotonic(), embedding)
            self._embedding_cache.move_to_end(key)
            while len(self._embedding_cache) > 128:
                self._embedding_cache.popitem(last=False)
        return embedding

    def _remember(self, item):
        bucket = self.recent_messages.setdefault(item.scope, OrderedDict())
        bucket[item.message_id] = item
        # History seeding may arrive after newer gateway events.
        bucket = OrderedDict(sorted(bucket.items(), key=lambda pair: (pair[1].created_at, pair[0])))
        while len(bucket) > self.MAX_RECENT:
            bucket.popitem(last=False)
        self.recent_messages[item.scope] = bucket
        self.recent_messages.move_to_end(item.scope)
        while len(self.recent_messages) > self.MAX_CHANNELS:
            scope, _ = self.recent_messages.popitem(last=False)
            self._seeded.discard(scope)

    def observe(self, message):
        """Cheap in-memory ingestion only; this does not store durable memory."""
        if message.author.bot and message.author.id != self.bot.user.id:
            return
        if (message.content or "").lstrip().startswith(("!", "?")):
            return
        item = ContextMessage.from_message(message)
        if item.content:
            self._remember(item)

    def forget(self, guild_id, channel_id, message_id):
        scope = guild_id, channel_id
        self.recent_messages.get(scope, {}).pop(message_id, None)
        # A deleted exchange should not survive in conversational history.
        for key in list(self._exchanges):
            if key[:2] == scope:
                self._exchanges.pop(key)

    async def resolve_reply_parent(self, message):
        """Never resolve a reference outside the current channel/guild."""
        reference = getattr(message, "reference", None)
        parent_id = getattr(reference, "message_id", None)
        if parent_id is None:
            return None
        if getattr(reference, "channel_id", message.channel.id) != message.channel.id:
            return None
        scope = getattr(message.guild, "id", None), message.channel.id
        parent = self.recent_messages.get(scope, {}).get(parent_id)
        if parent is not None:
            return parent
        resolved = getattr(reference, "resolved", None)
        if resolved is None:
            try:
                resolved = await asyncio.wait_for(message.channel.fetch_message(parent_id), self.LOOKUP_TIMEOUT)
            except Exception as error:
                logger.debug("Reply parent unavailable channel=%s error=%s", scope[1], type(error).__name__)
                return None
        # Includes Discord's DeletedReferencedMessage sentinel.
        if not hasattr(resolved, "author") or resolved.id != parent_id:
            return None
        if (getattr(resolved.guild, "id", None), resolved.channel.id) != scope:
            return None
        parent = ContextMessage.from_message(resolved)
        self._remember(parent)
        return parent

    async def _reply_chain(self, message):
        parent = await self.resolve_reply_parent(message)
        chain = []
        seen = {message.id}
        scope = getattr(message.guild, "id", None), message.channel.id
        while parent is not None and parent.message_id not in seen:
            chain.append(parent)
            seen.add(parent.message_id)
            if len(chain) >= self.MAX_REPLY_DEPTH or parent.reply_to is None:
                break
            ancestor = self.recent_messages.get(scope, {}).get(parent.reply_to)
            if ancestor is None:
                try:
                    fetched = await asyncio.wait_for(message.channel.fetch_message(parent.reply_to), self.LOOKUP_TIMEOUT)
                    if (getattr(fetched.guild, "id", None), fetched.channel.id) != scope:
                        break
                    ancestor = ContextMessage.from_message(fetched)
                    if ancestor.message_id != parent.reply_to:
                        break
                    self._remember(ancestor)
                except Exception as error:
                    logger.debug("Reply ancestor unavailable error=%s", type(error).__name__)
                    break
            parent = ancestor
        return tuple(chain)

    async def _seed_recent(self, message, current):
        if current.scope in self._seeded:
            return
        # Mark before awaiting so concurrent requests don't refetch a channel.
        self._seeded.add(current.scope)
        try:
            async for previous in message.channel.history(limit=self.MAX_RECENT, before=message):
                if previous.created_at < current.created_at - self.WINDOW:
                    break
                if (getattr(previous.guild, "id", None), previous.channel.id) == current.scope:
                    self.observe(previous)
        except Exception as error:
            logger.debug("Recent history unavailable channel=%s error=%s", current.channel_id, type(error).__name__)

    def _select_recent(self, current, chain):
        candidates = [item for item in self.recent_messages.get(current.scope, {}).values()
                      if current.created_at - self.WINDOW <= item.created_at <= current.created_at
                      and item.message_id != current.message_id]
        bot_id = self.bot.user.id
        participants = {current.author_id, *current.mentioned_users}
        participants.update(item.author_id for item in chain)
        participants.discard(bot_id)
        connected = {current.message_id, *(item.message_id for item in chain)}
        # Iterate to follow multi-hop replies regardless of arrival order.
        for _ in range(self.MAX_REPLY_DEPTH):
            for item in candidates:
                if item.reply_to in connected or item.message_id in connected:
                    connected.add(item.message_id)
                    if item.reply_to is not None:
                        connected.add(item.reply_to)
        selected = []
        chain_ids = {item.message_id for item in chain}
        for item in candidates:
            linked = item.message_id in connected
            participant = not item.is_bot and (
                item.author_id in participants or bool(participants.intersection(item.mentioned_users))
            )
            # Explicit replies into another branch stay out even if a user
            # participates in both discussions. Bot messages need a reply link.
            if item.reply_to is not None and not linked:
                continue
            if item.message_id not in chain_ids and (linked or participant):
                selected.append(item)
        return tuple(selected[-self.MAX_SELECTED:])

    async def get_verified_rank(self, message):
        if not re.search(r"\b(rank(?:ed)?|tier|leaderboard)\b", message.content, re.IGNORECASE):
            return None
        target = next((user for user in message.mentions if user.id != self.bot.user.id), message.author)
        try:
            rank = await asyncio.wait_for(self.bot.db.get_player_rank(target.id), self.LOOKUP_TIMEOUT)
            rank = rank.strip() if isinstance(rank, str) and rank.strip() else "Unranked"
        except Exception as error:
            logger.warning("Rank lookup unavailable error=%s", type(error).__name__)
            rank = None
        return VerifiedRank(target.id, target.display_name[:100], rank)

    def remember_exchange(self, message, user_text, reply_text):
        key = getattr(message.guild, "id", None), message.channel.id, message.author.id
        exchanges = self._exchanges.setdefault(key, [])
        exchanges.append((message.created_at, message.id, user_text[:2000], reply_text[:2000]))
        self._exchanges[key] = exchanges[-5:]
        self._exchanges.move_to_end(key)
        while len(self._exchanges) > 256:
            self._exchanges.popitem(last=False)

    async def build_context(self, message, *, memory_channel_id=0):
        current = ContextMessage.from_message(message)
        self.observe(message)
        if self.needs_memory(current.content):
            try:
                await asyncio.wait_for(self._seed_recent(message, current), self.LOOKUP_TIMEOUT)
            except asyncio.TimeoutError:
                self._seeded.discard(current.scope)
        chain = await self._reply_chain(message)
        recent = self._select_recent(current, chain)
        guild = message.guild
        context = BrainContext(
            current=current,
            server_name=getattr(guild, "name", "Direct Message")[:100],
            channel_name=getattr(message.channel, "name", "Direct Message")[:100],
            author_roles=tuple(role.name[:100] for role in getattr(message.author, "roles", ()) if role.name != "@everyone")[:20],
            author_is_admin=bool(getattr(getattr(message.author, "guild_permissions", None), "administrator", False)),
            admins=tuple(member.display_name[:100] for member in getattr(guild, "members", ())
                         if not member.bot and member.guild_permissions.administrator)[:10],
            reply_chain=chain, recent_messages=recent,
            verified_rank=await self.get_verified_rank(message),
        )
        key = (*current.scope, current.author_id)
        selected_ids = {item.message_id for item in (*chain, *recent)}
        context.exchanges = tuple((user, reply) for timestamp, message_id, user, reply in self._exchanges.get(key, ())
                                  if message_id in selected_ids
                                  and current.created_at - self.WINDOW <= timestamp < current.created_at)
        try:
            # Preserve hot reload without blocking the Discord event loop.
            context.curated_lore = (await asyncio.to_thread(self.lore_path.read_text, encoding="utf-8"))[:12000]
        except FileNotFoundError:
            pass
        except OSError as error:
            logger.warning("Curated lore unavailable error=%s", type(error).__name__)
        if current.guild_id is not None and memory_channel_id and self.needs_memory(current.content):
            # Search approved general-channel memories while keeping the
            # current guild boundary. Short follow-ups include their parent.
            query = "\n".join([*(item.content[:500] for item in reversed(chain)), current.content])[:3500]
            try:
                context.query_embedding = await self._cached_embedding(current.scope, query) if query.strip() else None
            except Exception as error:
                logger.warning("Embedding unavailable; using keyword retrieval error=%s", type(error).__name__)
            try:
                context.memories = await asyncio.wait_for(self.bot.db.get_chat_context_memories(
                    current.guild_id, current.channel_id, query, context.query_embedding,
                    source_channel_id=memory_channel_id,
                ), self.LOOKUP_TIMEOUT)
            except Exception as error:
                logger.warning("Memory retrieval unavailable error=%s", type(error).__name__)
        logger.debug("Context channel=%s parents=%s recent=%s memories=%s", current.channel_id,
                     len(chain), len(recent), len(context.memories))
        return context
