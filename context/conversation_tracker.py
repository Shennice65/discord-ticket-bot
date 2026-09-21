import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timedelta
import re
from dataclasses import dataclass

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
class ConversationExchange:
    message_id: int
    guild_id: int | None
    channel_id: int
    author_id: int
    author_name: str
    user_text: str
    bot_text: str
    created_at: datetime

class ConversationTracker:
    WINDOW = timedelta(minutes=15)
    LIVE_WINDOW = timedelta(minutes=2)
    MAX_CHANNELS = 128
    MAX_RECENT = 75
    MAX_SELECTED = 12
    MAX_LIVE_SELECTED = 6
    MAX_REPLY_DEPTH = 3

    def __init__(self, bot):
        self.bot = bot
        self.recent_messages = OrderedDict()
        self._exchanges = OrderedDict()

    def _remember(self, item):
        bucket = self.recent_messages.setdefault(item.scope, OrderedDict())
        bucket[item.message_id] = item
        bucket = OrderedDict(sorted(bucket.items(), key=lambda pair: (pair[1].created_at, pair[0])))
        while len(bucket) > self.MAX_RECENT:
            bucket.popitem(last=False)
        self.recent_messages[item.scope] = bucket
        self.recent_messages.move_to_end(item.scope)
        while len(self.recent_messages) > self.MAX_CHANNELS:
            self.recent_messages.popitem(last=False)

    def observe(self, message):
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
        for key in list(self._exchanges):
            if key[:2] == scope:
                self._exchanges.pop(key)

    async def resolve_reply_parent(self, message):
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
            if item.reply_to is not None and not linked:
                continue
            if item.message_id not in chain_ids and item.message_id not in live_ids and (linked or participant):
                selected.append(item)
        return tuple(selected[-self.MAX_SELECTED:])

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
