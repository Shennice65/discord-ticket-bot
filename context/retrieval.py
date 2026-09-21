import re
import logging
from memory.memory_service import MemoryService

logger = logging.getLogger(__name__)

_QUERY_STOP_WORDS = {
    "the", "and", "are", "who", "what", "how", "why", "when", "where", "does", "do", "did",
    "you", "bot", "bro", "hey", "hello", "hi", "help", "please", "this", "that", "with",
    "from", "for", "about", "can", "could", "would", "have", "has", "not", "was", "is",
}

class MemoryRetriever:
    def __init__(self, bot):
        self.memory_service = MemoryService(bot)
        self.memory_cache = {}
        self._memory_cache_channel_id = None

    async def refresh_cache(self, channel_id: int):
        records = await self.memory_service.load_memory_cache(channel_id)
        grouped = {}
        for record in records:
            guild_id = record.get("guild_id")
            if guild_id is not None:
                grouped.setdefault(guild_id, []).append(record)
        self.memory_cache = grouped
        self._memory_cache_channel_id = channel_id

    def select_cached_memories(self, current_msg, chain=()):
        records = [record for record in self.memory_cache.get(current_msg.guild_id, ())
                   if self._memory_cache_channel_id is None
                   or record.get("channel_id") == self._memory_cache_channel_id]
        if not records:
            return []
            
        query = " ".join([item.content for item in reversed(chain)] + [current_msg.content]).casefold()
        words = {
            word for word in re.findall(r"\w{3,}", query)
            if word not in _QUERY_STOP_WORDS
        }
        if not words:
            return []
        ranked = []
        
        for record in records:
            searchable = " ".join(str(record.get(field, "")) for field in
                                   ("summary", "memory_key", "associated_users")).casefold()
            searchable_words = set(re.findall(r"\w{3,}", searchable))
            overlap = len(words & searchable_words)
            if overlap <= 0:
                continue
            score = overlap * 0.5 + float(record.get("confidence", 0) or 0) * 0.3
            score += float(record.get("importance", 0) or 0) * 0.2
            ranked.append((score, record))
            
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [record for score, record in ranked[:3] if score > 0]
