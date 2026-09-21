"""Read-only context retrieval over the existing chat_memory collection."""

import logging
import re

logger = logging.getLogger(__name__)


class ChatContextMixin:
    async def get_chat_context_memories(self, guild_id, channel_id, query, embedding=None):
        if self.chat_memory is None:
            return []
        scope = {"guild_id": guild_id, "channel_id": channel_id}
        fields = {"guild_id": 1, "channel_id": 1, "user_text": 1, "bot_reply": 1,
                  "summary": 1, "source_message_ids": 1, "confidence": 1, "record_type": 1}
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
        selected = []
        seen = set()
        for record in results:
            if record.get("guild_id") != guild_id or record.get("channel_id") != channel_id:
                continue
            identity = str(record.get("_id", record))
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(record)
            if len(selected) == 3:
                break
        logger.debug("Memory retrieval channel=%s selected=%s", channel_id, len(selected))
        return selected
