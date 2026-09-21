"""Read-only context retrieval over the existing chat_memory collection."""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ChatContextMixin:
    async def get_chat_context_memories(
        self, guild_id, channel_id, query, embedding=None, source_channel_id=None
    ):
        if self.chat_memory is None:
            return []
        lookup_channel_id = source_channel_id or channel_id
        scope = {"guild_id": guild_id, "channel_id": lookup_channel_id}
        fields = {"guild_id": 1, "channel_id": 1, "user_text": 1, "bot_reply": 1,
                  "summary": 1, "memory_key": 1, "associated_users": 1,
                  "source_message_ids": 1, "confidence": 1, "importance": 1,
                  "record_type": 1, "timestamp": 1}
        results = []
        if embedding:
            try:
                results = await self.chat_memory.aggregate([
                    {"$vectorSearch": {"index": "vector_index", "path": "embedding",
                                       "queryVector": embedding, "numCandidates": 1000,
                                       "limit": 3, "filter": scope}},
                    {"$project": fields},
                ]).to_list(length=3)
            except Exception as error:
                logger.debug("Scoped vector retrieval unavailable; trying keywords error=%s", type(error).__name__)
        # A missing Atlas filter index must not trigger a global vector query.
        # Literal keywords also cover useful records without embeddings.
        stop_words = {"what", "when", "where", "which", "that", "this", "with", "then", "rank", "have"}
        words = list(dict.fromkeys(word for word in re.findall(r"\w{3,}", query.casefold())
                                   if word not in stop_words))[-12:]
        if words:
            pattern = "|".join(re.escape(word) for word in words)
            try:
                matches = await self.chat_memory.find(
                    {**scope, "$or": [{field: {"$regex": pattern, "$options": "i"}}
                                      for field in ("summary", "user_text", "bot_reply")]}, fields,
                ).sort("timestamp", -1).limit(3).to_list(length=3)
                results = matches + results
            except Exception as error:
                logger.debug("Keyword retrieval unavailable error=%s", type(error).__name__)
        query_words = set(words)
        now = datetime.now(timezone.utc)
        ranked = []
        for position, record in enumerate(results):
            if record.get("guild_id") != guild_id or record.get("channel_id") != lookup_channel_id:
                continue
            confidence = float(record.get("confidence", 0) or 0)
            if source_channel_id != channel_id and confidence < 0.5:
                continue
            searchable = " ".join(str(record.get(field, "")) for field in
                                   ("summary", "user_text", "bot_reply", "memory_key", "associated_users")).casefold()
            overlap = sum(word in searchable for word in query_words) / max(len(query_words), 1)
            timestamp = record.get("timestamp")
            age_days = max(0.0, (now - timestamp).total_seconds() / 86400) if isinstance(timestamp, datetime) else 30.0
            recency = 1.0 / (1.0 + age_days / 30.0)
            vector_bonus = max(0.0, 0.1 - position * 0.01)
            score = overlap * 0.45 + confidence * 0.25 + float(record.get("importance", 0) or 0) * 0.15 + recency * 0.15 + vector_bonus
            ranked.append((score, record))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected = []
        seen = set()
        for _score, record in ranked:
            identity = str(record.get("_id", record))
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(record)
            if len(selected) == 3:
                break
        logger.debug("Memory retrieval channel=%s selected=%s", lookup_channel_id, len(selected))
        return selected
