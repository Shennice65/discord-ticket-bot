"""Engagement-tier scoring and style fingerprinting for adaptive AI responses.

All analysis is rule-based (zero LLM calls). Style fingerprints are derived
from regex/counting on existing chat_messages. Engagement scores use MongoDB
aggregation on ai_audit, chat_messages, and player_ranks.
"""

import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Common slang tokens used in gaming Discord communities.
_SLANG_TOKENS = frozenset({
    "fr", "ngl", "ong", "bruh", "lol", "lmao", "lmfao", "istg", "icl",
    "idk", "tbh", "smh", "imo", "fam", "lowkey", "highkey", "deadass",
    "cap", "nocap", "bet", "bussin", "sus", "vibe", "mid", "w", "l",
    "gg", "ez", "rip", "goat", "goated", "cracked", "diff", "ratio",
})

_EMOJI_PATTERN = re.compile(
    r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
    r"\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF"
    r"\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF]"
)


class StyleFingerprint:
    """Rule-based speech-pattern analysis. No LLM calls."""

    __slots__ = ("caps_style", "punctuation", "avg_length", "emoji_heavy", "slang_markers")

    def __init__(self, messages: list):
        texts = [str(m) for m in messages if str(m).strip()]
        if not texts:
            self.caps_style = "normal"
            self.punctuation = "none"
            self.avg_length = "short"
            self.emoji_heavy = False
            self.slang_markers = []
            return

        self.caps_style = self._detect_caps(texts)
        self.punctuation = self._detect_punctuation(texts)
        self.avg_length = self._detect_length(texts)
        self.emoji_heavy = self._detect_emoji(texts)
        self.slang_markers = self._detect_slang(texts)

    @staticmethod
    def _detect_caps(texts):
        lowercase_ratio = sum(1 for t in texts if t == t.lower()) / len(texts)
        if lowercase_ratio > 0.75:
            return "lowercase"
        uppercase_ratio = sum(1 for t in texts if t == t.upper() and len(t) > 3) / len(texts)
        if uppercase_ratio > 0.3:
            return "uppercase"
        return "normal"

    @staticmethod
    def _detect_punctuation(texts):
        period_ratio = sum(1 for t in texts if t.rstrip().endswith(".")) / len(texts)
        if period_ratio > 0.5:
            return "formal"
        expressive_ratio = sum(1 for t in texts if "!" in t or "??" in t) / len(texts)
        if expressive_ratio > 0.4:
            return "expressive"
        return "none"

    @staticmethod
    def _detect_length(texts):
        avg = sum(len(t.split()) for t in texts) / len(texts)
        if avg < 5:
            return "short"
        if avg < 15:
            return "medium"
        return "long"

    @staticmethod
    def _detect_emoji(texts):
        emoji_msgs = sum(1 for t in texts if _EMOJI_PATTERN.search(t))
        return emoji_msgs / len(texts) > 0.3

    @staticmethod
    def _detect_slang(texts):
        counter = Counter()
        for text in texts:
            words = set(text.lower().split())
            for word in words & _SLANG_TOKENS:
                counter[word] += 1
        return [word for word, _ in counter.most_common(3) if counter[word] >= 2]

    def to_prompt_hint(self) -> str:
        """Return a single-line style instruction (~10-15 tokens)."""
        parts = []
        if self.caps_style == "lowercase":
            parts.append("use lowercase")
        elif self.caps_style == "uppercase":
            parts.append("match their loud energy with caps")

        if self.punctuation == "none":
            parts.append("skip end punctuation")
        elif self.punctuation == "formal":
            parts.append("use proper punctuation")

        if self.avg_length == "short":
            parts.append("keep it very brief")
        elif self.avg_length == "long":
            parts.append("you can be more detailed")

        if self.emoji_heavy:
            parts.append("use emoji sparingly")

        if not parts:
            return ""
        return "Mirror the user's vibe: " + ", ".join(parts)


# ── Tier definitions ────────────────────────────────────────────────────

TIER_PARAMS = {
    "casual": {
        "max_tokens": 100,
        "max_history": 0,
        "tools_enabled": False,
        "temperature": 0.5,
    },
    "regular": {
        "max_tokens": 200,
        "max_history": 1,
        "tools_enabled": True,
        "temperature": 0.6,
    },
    "core": {
        "max_tokens": 400,
        "max_history": 3,
        "tools_enabled": True,
        "temperature": 0.7,
    },
}


class UserProfile:
    """Resolved engagement profile for a single request."""

    __slots__ = (
        "tier", "score", "max_tokens", "max_history",
        "tools_enabled", "temperature", "style_hint",
    )

    def __init__(self, tier, score, style_hint=""):
        self.tier = tier
        self.score = score
        params = TIER_PARAMS[tier]
        self.max_tokens = params["max_tokens"]
        self.max_history = params["max_history"]
        self.tools_enabled = params["tools_enabled"]
        self.temperature = params["temperature"]
        self.style_hint = style_hint


class UserEngagementScorer:
    """Compute engagement tier and style fingerprint from existing DB data.

    All scoring is MongoDB aggregation + arithmetic — zero LLM calls.
    Results are cached in-memory and refreshed every ``CACHE_TTL_SECONDS``.
    """

    CACHE_TTL_SECONDS = 600  # 10 minutes
    STYLE_SAMPLE_LIMIT = 50  # messages to analyse for fingerprinting

    def __init__(self, bot):
        self.bot = bot
        self._cache = {}       # user_id → (UserProfile, expires_at)
        self._style_cache = {} # user_id → (StyleFingerprint, expires_at)

    # ── Public API ──────────────────────────────────────────────────────

    async def get_profile(self, user_id: int, member=None) -> UserProfile:
        """Return the engagement profile for a user, from cache if fresh."""
        now = time.monotonic()
        cached = self._cache.get(user_id)
        if cached and now < cached[1]:
            return cached[0]

        score = await self._compute_score(user_id, member)
        tier = self._score_to_tier(score)
        style_hint = ""
        if tier == "core":
            fingerprint = await self._get_fingerprint(user_id)
            style_hint = fingerprint.to_prompt_hint() if fingerprint else ""

        profile = UserProfile(tier, score, style_hint)
        self._cache[user_id] = (profile, now + self.CACHE_TTL_SECONDS)
        return profile

    def invalidate(self, user_id: int) -> None:
        """Force a refresh on next request (e.g. after admin override)."""
        self._cache.pop(user_id, None)
        self._style_cache.pop(user_id, None)

    # ── Scoring ─────────────────────────────────────────────────────────

    async def _compute_score(self, user_id: int, member=None) -> float:
        """Engagement score from 0..30+. Higher = more engaged.

        Components (all from existing collections):
        - ai_interactions_7d:  count of AI audit events in last 7 days (0-10 pts)
        - chat_messages_7d:    count of chat_messages in last 7 days (0-8 pts)
        - is_ranked:           has an entry in player_ranks (5 pts)
        - membership_days:     days since joining the server (0-5 pts)
        """
        score = 0.0
        db = getattr(self.bot, "db", None)
        if not db or getattr(db, "db", None) is None:
            return score

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        # 1. AI interaction frequency (last 7 days) — from ai_audit
        try:
            audit_collection = db.db.ai_audit
            ai_count = await audit_collection.count_documents(
                {"created_at": {"$gte": cutoff}, "usage.total_tokens": {"$gt": 0}},
            )
            # Rough per-user approximation: we don't track user_id in ai_audit
            # today, so we fall back to chat_messages for user-specific counts.
            # When user_id is added to audit, swap this.
        except Exception:
            ai_count = 0

        # 2. User's chat message count (last 7 days) — from chat_messages
        try:
            msg_count = await db.chat_messages.count_documents(
                {"author_id": user_id, "created_at": {"$gte": cutoff}},
            )
            score += min(msg_count / 3, 10.0)   # 30 msgs in 7d = max 10 pts
        except Exception:
            pass

        # 3. Is ranked? — from player_ranks
        try:
            rank_doc = await db.player_ranks.find_one({"user_id": user_id})
            if rank_doc and rank_doc.get("rank"):
                score += 5.0
        except Exception:
            pass

        # 4. Membership tenure
        if member is not None:
            joined = getattr(member, "joined_at", None)
            if joined:
                if joined.tzinfo is None:
                    joined = joined.replace(tzinfo=timezone.utc)
                days = (datetime.now(timezone.utc) - joined).days
                score += min(days / 30, 5.0)  # 150+ days = max 5 pts

        return round(score, 2)

    @staticmethod
    def _score_to_tier(score: float) -> str:
        if score >= 15:
            return "core"
        if score >= 5:
            return "regular"
        return "casual"

    # ── Style fingerprinting ────────────────────────────────────────────

    async def _get_fingerprint(self, user_id: int) -> StyleFingerprint:
        """Build or return cached style fingerprint from chat_messages."""
        now = time.monotonic()
        cached = self._style_cache.get(user_id)
        if cached and now < cached[1]:
            return cached[0]

        db = getattr(self.bot, "db", None)
        texts = []
        if db and getattr(db, "chat_messages", None) is not None:
            try:
                cursor = db.chat_messages.find(
                    {"author_id": user_id},
                    {"content": 1},
                ).sort("created_at", -1).limit(self.STYLE_SAMPLE_LIMIT)
                async for doc in cursor:
                    content = (doc.get("content") or "").strip()
                    if content and not content.startswith(("!", "?")):
                        texts.append(content)
            except Exception as error:
                logger.debug(
                    "Style fingerprint fetch failed user=%s error=%s",
                    user_id, type(error).__name__,
                )

        fingerprint = StyleFingerprint(texts)
        self._style_cache[user_id] = (fingerprint, now + self.CACHE_TTL_SECONDS)
        return fingerprint
