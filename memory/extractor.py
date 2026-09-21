import json
import logging
import re
import asyncio
from datetime import datetime, timezone
from google.genai import types
from ai.llm import llm

logger = logging.getLogger(__name__)

MEMORY_TYPES = {"event", "inside_joke", "nickname", "relationship", "server_lore", "community_term"}

class MemoryExtractor:
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _parse(text):
        text = (text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return None
        if isinstance(payload, dict):
            if "memories" not in payload:
                return None
            payload = payload["memories"]
        if not isinstance(payload, list):
            return None
        candidates = []
        for item in payload:
            if not isinstance(item, dict) or item.get("type") not in MEMORY_TYPES:
                continue
            summary = str(item.get("summary") or "").strip()[:1000]
            if not summary:
                continue
            source_ids = item.get("source_message_ids") or []
            try:
                confidence = float(item.get("confidence", 0.25))
                importance = float(item.get("importance", 0.25))
            except (TypeError, ValueError):
                confidence, importance = 0.25, 0.25
            candidates.append({
                "record_type": item["type"],
                "memory_key": str(item.get("name") or summary).strip().casefold()[:180],
                "summary": summary,
                "associated_users": [str(value)[:100] for value in (item.get("associated_users") or [])[:20]],
                "source_message_ids": list(dict.fromkeys(source_ids))[:30],
                "confidence": max(0.0, min(0.95, confidence)),
                "importance": max(0.0, min(1.0, importance)),
            })
        return candidates

    async def extract(self, records):
        evidence = "\n".join(
            f"[{item.get('message_id')}] author_id={item.get('author_id')} "
            f"author_name={item.get('author_name', '')} "
            f"{item.get('user_text') or item.get('content', '')}"[:2200]
            for item in records
        )
        prompt = (
            "Extract only durable community memories from this Discord evidence. "
            "Return JSON only as {\"memories\":[...]}. Valid types: event, inside_joke, nickname, "
            "relationship, server_lore, community_term. Ignore ordinary chat, insults, questions, and "
            "one-off claims. Never use bot messages as evidence. Each item must include type, name, summary, "
            "associated_users, source_message_ids chosen only from the IDs shown, confidence, and importance. "
            "A single supporting message must have confidence <= 0.35.\n\nEVIDENCE:\n" + evidence
        )
        try:
            response = await llm.generate_content(model="gemini-3.5-flash", contents=prompt)
            return self._parse(getattr(response, "text", ""))
        except Exception as error:
            logger.warning("Memory extraction unavailable error=%s", type(error).__name__)
            return None

    async def persist(self, db, records, candidates):
        if not candidates or getattr(db, "chat_memory", None) is None:
            return 0
            
        await llm.ensure_keys(db)
            
        embeddings = []
        try:
            # We use the generic client from our llm wrapper to get embeddings
            if llm.client:
                response = await asyncio.wait_for(
                    llm.client.aio.models.embed_content(
                        model="gemini-embedding-2",
                        contents=[item["summary"] for item in candidates],
                        config=types.EmbedContentConfig(output_dimensionality=256),
                    ),
                    timeout=15,
                )
                embeddings = [list(item.values) for item in getattr(response, "embeddings", ())]
        except Exception as error:
            logger.debug("Memory embedding unavailable error=%s", type(error).__name__)
            
        ids = {item.get("message_id") for item in records}
        id_strings = {str(value) for value in ids}
        first_seen = min((item.get("timestamp") or item.get("created_at") for item in records), default=datetime.now(timezone.utc))
        last_seen = max((item.get("timestamp") or item.get("created_at") for item in records), default=first_seen)
        stored = 0
        for index, candidate in enumerate(candidates):
            source_ids = [value for value in candidate["source_message_ids"]
                          if value in ids or str(value) in id_strings]
            if not source_ids:
                continue
            confidence = min(0.35, candidate["confidence"]) if len(source_ids) == 1 else max(candidate["confidence"], 0.5)
            query = {"guild_id": records[0].get("guild_id"), "channel_id": records[0].get("channel_id"),
                     "memory_key": candidate["memory_key"]}
            existing = await db.chat_memory.find_one(query)
            if existing:
                prior_ids = set(existing.get("source_message_ids") or [])
                new_ids = [value for value in source_ids if value not in prior_ids]
                confidence = max(confidence, float(existing.get("confidence", 0)))
                if new_ids:
                    confidence = min(0.99, confidence + 0.15)
                source_ids = list(dict.fromkeys((existing.get("source_message_ids") or []) + source_ids))
                existing_first = existing.get("first_seen") or first_seen
                existing_last = existing.get("last_seen") or last_seen
                first_seen = min(existing_first, first_seen)
                last_seen = max(existing_last, last_seen)
            fields = {"record_type": candidate["record_type"], "summary": candidate["summary"],
                      "associated_users": candidate["associated_users"], "source_message_ids": source_ids,
                      "confidence": confidence, "importance": candidate["importance"],
                      "first_seen": first_seen, "last_seen": last_seen, "timestamp": last_seen}
            if len(embeddings) > index:
                fields["embedding"] = embeddings[index]
            await db.chat_memory.update_one(
                query,
                {"$set": fields},
                upsert=True,
            )
            stored += 1
        return stored

    async def process(self, db, records):
        records = [item for item in records if not item.get("author_bot") and not item.get("is_bot")]
        if not records:
            return True, 0
        candidates = await self.extract(records)
        if candidates is None:
            return False, 0
        return True, await self.persist(db, records, candidates)
