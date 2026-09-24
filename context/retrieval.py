import re
import logging
import math
from memory.memory_service import MemoryService
from ai.llm import llm
from framework.memory import MemoryScope

logger = logging.getLogger(__name__)

_QUERY_STOP_WORDS = {
    "the", "and", "are", "who", "what", "how", "why", "when", "where", "does", "do", "did",
    "you", "bot", "bro", "hey", "hello", "hi", "help", "please", "this", "that", "with",
    "from", "for", "about", "can", "could", "would", "have", "has", "not", "was", "is",
    "above", "below", "person", "someone", "somebody", "name", "here", "there", "just", "tell", "me",
}

def _cosine_similarity(vec1, vec2):
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm_a = math.sqrt(sum(a * a for a in vec1))
    norm_b = math.sqrt(sum(b * b for b in vec2))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)

class MemoryRetriever:
    def __init__(self, bot):
        self.memory_service = MemoryService(bot)
        self.memory_cache = {}
        self.entity_graph = {}
        self._memory_cache_channel_id = None

    async def refresh_cache(self):
        records = await self.memory_service.load_memory_cache()
        grouped = {}
        graph = {}
        for record in records:
            guild_id = record.get("guild_id")
            if guild_id is not None:
                grouped.setdefault(guild_id, []).append(record)
                
                guild_graph = graph.setdefault(guild_id, {})
                for user in record.get("associated_users", []):
                    name = str(user).casefold().strip()
                    if name:
                        guild_graph.setdefault(name, []).append(record)
                        
        self.memory_cache = grouped
        self.entity_graph = graph

    async def select_cached_memories(self, current_msg, chain=(), excluded_terms=()):
        if current_msg.guild_id is None:
            return []
            
        guild_id = current_msg.guild_id
        scope = MemoryScope(
            guild_id,
            getattr(current_msg, "channel_id", None) or self._memory_cache_channel_id,
            getattr(current_msg, "author_id", None),
        )
        records = [
            record for record in self.memory_cache.get(guild_id, [])
            if scope.matches_record(record)
        ]
        if not records:
            return []
            
        # 1. Fast Path: Entity Graph
        entity_matches = []
        guild_graph = self.entity_graph.get(guild_id, {})
        
        # Use @mentioned names for entity lookup, but NOT the author's own
        # display name — display names are mutable and could be impersonation.
        mentioned_names = [user.casefold() for user in getattr(current_msg, "mentioned_user_names", []) if user]
            
        current_text = re.sub(r"<@!?\d+>", "", current_msg.content or "").strip().casefold()
        
        for name in guild_graph.keys():
            if len(name) >= 3 and name in current_text:
                mentioned_names.append(name)
                
        seen_ids = set()
        for name in set(mentioned_names):
            if name in guild_graph:
                for record in guild_graph[name]:
                    rec_id = str(record.get("_id", record.get("memory_key")))
                    if rec_id and rec_id not in seen_ids:
                        try:
                            if float(record.get("confidence", 0) or 0) >= 0.1:
                                entity_matches.append(record)
                                seen_ids.add(rec_id)
                        except (TypeError, ValueError):
                            pass
                            
        # Sort entity matches by importance + confidence
        entity_matches.sort(key=lambda r: float(r.get("importance", 0) or 0) + float(r.get("confidence", 0) or 0), reverse=True)
        top_entities = entity_matches[:2]
        
        # 2. Semantic Path: Vector Search
        words = re.findall(r"\w{3,}", current_text)
        query_words = set(words) - _QUERY_STOP_WORDS
        
        semantic_scores = {}
        if query_words or len(current_text) > 10:
            query = " ".join(
                [item.content for item in reversed(chain[-2:])
                 if not getattr(item, "is_bot", False) and item.guild_id == guild_id
                 and item.channel_id == current_msg.channel_id] + [current_text]
            ).strip()
            
            if len(query) > 5:
                try:
                    embeddings = await llm.embed([query])
                    if embeddings:
                        query_vec = embeddings[0]
                        for record in records:
                            rec_id = str(record.get("_id", record.get("memory_key")))
                            if rec_id in seen_ids:
                                continue
                                
                            rec_emb = record.get("embedding")
                            if not rec_emb or len(rec_emb) != len(query_vec):
                                continue
                                
                            sim = _cosine_similarity(query_vec, rec_emb)
                            if sim > 0.65:
                                semantic_scores[rec_id] = sim
                except Exception as e:
                    logger.warning(f"Semantic search failed: {type(e).__name__}")
                    
        # 3. Keyword Search Path (Always run if we have query words)
        keyword_scores = {}
        if query_words:
            for record in records:
                rec_id = str(record.get("_id", record.get("memory_key")))
                if rec_id in seen_ids: continue
                searchable = " ".join(str(record.get(field, "")) for field in ("summary", "memory_key")).casefold()
                searchable_words = set(re.findall(r"\w{3,}", searchable))
                overlap = len(query_words & searchable_words)
                if overlap > 0:
                    score = min(1.0, (overlap * 0.4) + float(record.get("confidence", 0) or 0) * 0.2)
                    keyword_scores[rec_id] = score
        
        # Merge scores and rank
        hybrid_matches = []
        for record in records:
            rec_id = str(record.get("_id", record.get("memory_key")))
            if rec_id in seen_ids: continue
            
            sem_score = semantic_scores.get(rec_id, 0.0)
            kw_score = keyword_scores.get(rec_id, 0.0)
            
            final_score = max(sem_score, kw_score)
            if final_score > 0:
                hybrid_matches.append((final_score, record))
                
        hybrid_matches.sort(key=lambda x: x[0], reverse=True)
        results = top_entities + [rec for _, rec in hybrid_matches[:2]]
        
        # Filter excluded terms out of results just in case
        excluded = {item.casefold() for item in excluded_terms if item}
        final_results = []
        for r in results[:3]:
            text = str(r.get("summary", "")).casefold()
            if not any(ex in text for ex in excluded):
                final_results.append(r)
                
        return final_results
