import json
import re
from dataclasses import dataclass
from context.conversation_tracker import ContextMessage
from context.retrieval import MemoryRetriever
import asyncio
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

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
    reply_chain: tuple[ContextMessage, ...] = ()
    immediate_preceding: ContextMessage | None = None
    surrounding_messages: tuple[ContextMessage, ...] = ()
    recent_messages: tuple[ContextMessage, ...] = ()
    exchanges: tuple = ()
    memories: list[dict] = None
    verified_rank: dict = None
    curated_lore: str = ""
    identity_correction: IdentityCorrection | None = None

class ContextBuilder:
    _CORRECTION_PATTERNS = (
        re.compile(r"\b(?:i['’]?m|i am)\s+not\s+(?:even\s+)?([^,.!?\n]{1,80})", re.IGNORECASE),
        re.compile(r"\b(?:that['’]?s|that is)\s+not\s+me\b", re.IGNORECASE),
        re.compile(r"\bwrong\s+person\b", re.IGNORECASE),
        re.compile(r"\bnot\s+me\b", re.IGNORECASE),
    )

    def __init__(self, bot, tracker, retriever):
        self.bot = bot
        self.tracker = tracker
        self.retriever = retriever
        self.lore_path = Path("lore.txt")
        self.curated_lore = ""
        self._lore_mtime = None

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

    def detect_identity_correction(self, content):
        text = (content or "").strip()
        if not text:
            return None
        match = self._CORRECTION_PATTERNS[0].search(text)
        if match:
            label = re.sub(r"\s+", " ", match.group(1)).strip(" '`")
            return IdentityCorrection(text[:200], label[:80] or None)
        if any(pattern.search(text) for pattern in self._CORRECTION_PATTERNS[1:]):
            return IdentityCorrection(text[:200])
        return None

    async def get_verified_rank(self, message):
        if not re.search(r"\b(rank(?:ed)?|tier|leaderboard)\b", message.content, re.IGNORECASE):
            return None
        target = next((user for user in message.mentions if user.id != self.bot.user.id), message.author)
        try:
            rank = await asyncio.wait_for(self.bot.db.get_player_rank(target.id), 1.0)
            rank = rank.strip() if isinstance(rank, str) and rank.strip() else "Unranked"
        except Exception as error:
            rank = None
        return {"user_id": target.id, "name": target.display_name[:100], "rank": rank} if rank else None

    async def build(self, message):
        current = ContextMessage.from_message(message)
        self.tracker.observe(message)
        
        chain = await self.tracker._reply_chain(message)
        live = self.tracker.select_live_window(current, chain)
        recent = self.tracker._select_recent(current, chain, live)
        
        guild = message.guild
        context = BrainContext(
            current=current,
            server_name=getattr(guild, "name", "Direct Message")[:100],
            channel_name=getattr(message.channel, "name", "Direct Message")[:100],
            author_roles=tuple(role.name[:100] for role in getattr(message.author, "roles", ()) if role.name != "@everyone")[:20],
            author_is_admin=bool(getattr(getattr(message.author, "guild_permissions", None), "administrator", False)),
            admins=tuple(member.display_name[:100] for member in getattr(guild, "members", ())
                         if not member.bot and member.guild_permissions.administrator)[:10],
            reply_chain=chain, 
            immediate_preceding=self.tracker.immediate_preceding(current),
            surrounding_messages=live, 
            recent_messages=recent,
            verified_rank=await self.get_verified_rank(message),
        )
        
        key = (*current.scope, current.author_id)
        selected_ids = {item.message_id for item in (*chain, *live, *recent)}
        
        context.exchanges = tuple(exchange for exchange in self.tracker._exchanges.get(key, ())
                                  if exchange.message_id in selected_ids
                                  and current.created_at - self.tracker.WINDOW <= exchange.created_at < current.created_at)
                                  
        context.curated_lore = self.curated_lore
        context.identity_correction = self.tracker.identity_correction(
            current, self.detect_identity_correction(current.content)
        )
        rejected = (context.identity_correction.rejected_label,) if context.identity_correction else ()
        context.memories = await self.retriever.select_cached_memories(current, chain, rejected)
        
        return context
