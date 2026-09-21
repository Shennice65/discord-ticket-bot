"""Bounded conversational context for the existing chat cog (no Gemini SDK)."""

import asyncio
import logging
import re
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
    mentioned_user_names: tuple[str, ...] = ()

    @property
    def scope(self):
        return self.guild_id, self.channel_id

    @classmethod
    def from_message(cls, message):
        reference = getattr(message, "reference", None)
        attachments = getattr(message, "attachments", ())
        mentions = tuple(getattr(message, "mentions", ()) or ())
        return cls(
            message.id, getattr(message.guild, "id", None), message.channel.id,
            message.author.id, message.author.display_name[:100],
            (message.content or ("[Attachment/Image]" if attachments else ""))[:2000],
            message.created_at, getattr(reference, "message_id", None),
            tuple(user.id for user in mentions), message.author.bot,
            tuple(getattr(user, "display_name", getattr(user, "name", str(user.id)))[:100]
                  for user in mentions),
        )


@dataclass(frozen=True)
class VerifiedRank:
    user_id: int
    name: str
    rank: str | None  # None means unavailable, not unranked.


@dataclass(frozen=True)
class ConversationExchange:
    message_id: int
    guild_id: int | None
    channel_id: int
    author_id: int
    author_name: str
    user_text: str
    bot_text: str
    created_at: datetime


@dataclass(frozen=True)
class IdentityCorrection:
    text: str
    rejected_label: str | None = None


@dataclass
class BrainContext:
    current: ContextMessage
    server_name: str
    channel_name: str
    author_roles: tuple[str, ...]
    author_is_admin: bool
    admins: tuple[str, ...]
    reply_chain: tuple[ContextMessage, ...] = ()  # Immediate parent first.
    surrounding_messages: tuple[ContextMessage, ...] = ()  # Chronological live window.
    recent_messages: tuple[ContextMessage, ...] = ()
    exchanges: tuple[ConversationExchange, ...] = ()
    memories: list[dict] = field(default_factory=list)
    verified_rank: VerifiedRank | None = None
    curated_lore: str = ""
    query_embedding: list[float] | None = None
    identity_correction: IdentityCorrection | None = None


class ServerBrain:
    """Own context selection; the cog supplies Discord events and an embed adapter."""

    WINDOW = timedelta(minutes=15)
    LIVE_WINDOW = timedelta(minutes=2)
    MAX_CHANNELS = 128
    MAX_RECENT = 75
    MAX_SELECTED = 12
    MAX_LIVE_SELECTED = 6
    MAX_REPLY_DEPTH = 3
    LOOKUP_TIMEOUT = 1.0
    _CORRECTION_PATTERNS = (
        re.compile(r"\b(?:i['’]?m|i am)\s+not\s+(?:even\s+)?([^,.!?\n]{1,80})", re.IGNORECASE),
        re.compile(r"\b(?:that['’]?s|that is)\s+not\s+me\b", re.IGNORECASE),
        re.compile(r"\bwrong\s+person\b", re.IGNORECASE),
        re.compile(r"\bnot\s+me\b", re.IGNORECASE),
    )

    def __init__(self, bot, embed_query, lore_path="lore.txt"):
        self.bot = bot
        self.embed_query = embed_query
        self.lore_path = Path(lore_path)
        self.recent_messages = OrderedDict()
        self._exchanges = OrderedDict()
        self.curated_lore = ""
        self._lore_mtime = None
        self.memory_cache = {}
        self._memory_cache_channel_id = None

    @classmethod
    def detect_identity_correction(cls, content):
        text = (content or "").strip()
        if not text:
            return None
        match = cls._CORRECTION_PATTERNS[0].search(text)
        if match:
            label = re.sub(r"\s+", " ", match.group(1)).strip(" '`")
            return IdentityCorrection(text[:200], label[:80] or None)
        if any(pattern.search(text) for pattern in cls._CORRECTION_PATTERNS[1:]):
            return IdentityCorrection(text[:200])
        return None

    async def refresh_memory_cache(self, channel_id):
        if not channel_id or not getattr(self.bot, "db", None):
            self.memory_cache = {}
            return
        loader = getattr(self.bot.db, "load_chat_memory_cache", None)
        if loader is None:
            return
        try:
            records = await loader(channel_id, minimum_confidence=0.5, limit=250)
        except Exception as error:
            logger.debug("Memory cache loader failed error=%s", type(error).__name__)
            return
        grouped = {}
        for record in records:
            guild_id = record.get("guild_id")
            if guild_id is not None:
                grouped.setdefault(guild_id, []).append(record)
        self.memory_cache = grouped
        self._memory_cache_channel_id = channel_id

    def select_cached_memories(self, current, chain=()):
        records = self.memory_cache.get(current.guild_id, ())
        if not records:
            return []
        query = " ".join([item.content for item in reversed(chain)] + [current.content]).casefold()
        words = set(re.findall(r"\w{3,}", query))
        ranked = []
        for record in records:
            searchable = " ".join(str(record.get(field, "")) for field in
                                   ("summary", "memory_key", "associated_users")).casefold()
            overlap = sum(word in searchable for word in words)
            score = overlap * 0.5 + float(record.get("confidence", 0) or 0) * 0.3
            score += float(record.get("importance", 0) or 0) * 0.2
            ranked.append((score, record))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [record for score, record in ranked[:3] if score > 0]

    async def refresh_lore_cache(self):
        try:
            mtime = self.lore_path.stat().st_mtime_ns
            if mtime == self._lore_mtime:
                return
            self.curated_lore = (await asyncio.to_thread(
                self.lore_path.read_text, encoding="utf-8"
            ))[:12000]
            self._lore_mtime = mtime
        except FileNotFoundError:
            self.curated_lore = ""
            self._lore_mtime = None
        except OSError as error:
            logger.debug("Curated lore refresh unavailable error=%s", type(error).__name__)

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
            self.recent_messages.popitem(last=False)

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
                break
            parent = ancestor
        return tuple(chain)

    def select_live_window(self, current, chain=()):
        """Return nearby same-channel messages using only the gateway cache.

        The result is chronological and intentionally independent of participants so a
        prompt such as "the person above me" still has the preceding speaker available.
        Reply-chain entries are represented by the higher-priority chain section and are
        omitted here to avoid spending the small live-window budget twice.
        """
        candidates = [item for item in self.recent_messages.get(current.scope, {}).values()
                      if item.message_id != current.message_id
                      and current.created_at - self.LIVE_WINDOW <= item.created_at <= current.created_at]
        chain_ids = {item.message_id for item in chain}
        candidates = [item for item in candidates if item.message_id not in chain_ids]
        candidates.sort(key=lambda item: (item.created_at, item.message_id))
        return tuple(candidates[-self.MAX_LIVE_SELECTED:])

    def _select_recent(self, current, chain, live=()):
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
        live_ids = {item.message_id for item in live}
        for item in candidates:
            linked = item.message_id in connected
            participant = not item.is_bot and (
                item.author_id in participants or bool(participants.intersection(item.mentioned_users))
            )
            # Explicit replies into another branch stay out even if a user
            # participates in both discussions. Bot messages need a reply link.
            if item.reply_to is not None and not linked:
                continue
            if item.message_id not in chain_ids and item.message_id not in live_ids and (linked or participant):
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
        exchanges.append(ConversationExchange(
            message_id=message.id,
            guild_id=getattr(message.guild, "id", None),
            channel_id=message.channel.id,
            author_id=message.author.id,
            author_name=message.author.display_name[:100],
            user_text=user_text[:2000],
            bot_text=reply_text[:2000],
            created_at=message.created_at,
        ))
        self._exchanges[key] = exchanges[-5:]
        self._exchanges.move_to_end(key)
        while len(self._exchanges) > 256:
            self._exchanges.popitem(last=False)

    async def build_context(self, message):
        current = ContextMessage.from_message(message)
        self.observe(message)
        chain = await self._reply_chain(message)
        live = self.select_live_window(current, chain)
        recent = self._select_recent(current, chain, live)
        guild = message.guild
        context = BrainContext(
            current=current,
            server_name=getattr(guild, "name", "Direct Message")[:100],
            channel_name=getattr(message.channel, "name", "Direct Message")[:100],
            author_roles=tuple(role.name[:100] for role in getattr(message.author, "roles", ()) if role.name != "@everyone")[:20],
            author_is_admin=bool(getattr(getattr(message.author, "guild_permissions", None), "administrator", False)),
            admins=tuple(member.display_name[:100] for member in getattr(guild, "members", ())
                         if not member.bot and member.guild_permissions.administrator)[:10],
            reply_chain=chain, surrounding_messages=live, recent_messages=recent,
            verified_rank=await self.get_verified_rank(message),
        )
        key = (*current.scope, current.author_id)
        selected_ids = {item.message_id for item in (*chain, *live, *recent)}
        context.exchanges = tuple(exchange for exchange in self._exchanges.get(key, ())
                                  if exchange.message_id in selected_ids
                                  and current.created_at - self.WINDOW <= exchange.created_at < current.created_at)
        context.curated_lore = self.curated_lore
        context.memories = self.select_cached_memories(current, chain)
        context.identity_correction = self.detect_identity_correction(current.content)
        logger.debug("Context channel=%s parents=%s live=%s recent=%s memories=%s", current.channel_id,
                     len(chain), len(live), len(recent), len(context.memories))
        return context
