import logging
from datetime import datetime, timezone, timedelta

from config import Config

logger = logging.getLogger(__name__)


class QuotaMixin:
    """Per-user rolling-window AI token quota tracking.

    Each user has a single document in ``user_quotas``.  The rolling window
    is controlled by ``AI_QUOTA_WINDOW_SECONDS`` (default 600 = 10 minutes).
    When a user's ``window_start`` is older than the window duration, their
    ``tokens_used`` is automatically reset to 0.
    """

    def _quota_window_seconds(self) -> int:
        return getattr(Config, "AI_QUOTA_WINDOW_SECONDS", 600)

    def _window_expired(self, doc) -> bool:
        """Return True if the stored window_start is older than the window."""
        window_start = doc.get("window_start")
        if window_start is None:
            return True
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - window_start).total_seconds()
        return elapsed >= self._quota_window_seconds()

    async def get_user_quota(self, user_id: int) -> dict:
        """Return the user's quota state, auto-resetting if the window expired."""
        doc = await self.user_quotas.find_one({"user_id": user_id})
        if doc and not self._window_expired(doc):
            window_start = doc["window_start"]
            if window_start.tzinfo is None:
                window_start = window_start.replace(tzinfo=timezone.utc)
            remaining = self._quota_window_seconds() - (
                datetime.now(timezone.utc) - window_start
            ).total_seconds()
            return {
                "tokens_used": doc.get("tokens_used", 0),
                "lifetime_tokens": doc.get("lifetime_tokens", 0),
                "limit_override": doc.get("limit_override"),
                "window_remaining_seconds": max(0, int(remaining)),
            }
        # Window expired or no doc → fresh window
        lifetime = doc.get("lifetime_tokens", 0) if doc else 0
        return {"tokens_used": 0, "lifetime_tokens": lifetime, "limit_override": None, "window_remaining_seconds": self._quota_window_seconds()}

    async def increment_user_quota(self, user_id: int, tokens: int) -> dict:
        """Atomically add tokens. Resets the window if it has expired."""
        now = datetime.now(timezone.utc)
        doc = await self.user_quotas.find_one({"user_id": user_id})

        if doc is None or self._window_expired(doc):
            # Start a fresh window with this usage
            result = await self.user_quotas.find_one_and_update(
                {"user_id": user_id},
                {
                    "$set": {
                        "tokens_used": tokens,
                        "window_start": now,
                        "updated_at": now,
                    },
                    "$inc": {"lifetime_tokens": tokens},
                    "$setOnInsert": {"user_id": user_id},
                },
                upsert=True,
                return_document=True,
            )
        else:
            # Increment within the current window
            result = await self.user_quotas.find_one_and_update(
                {"user_id": user_id},
                {
                    "$inc": {"tokens_used": tokens, "lifetime_tokens": tokens},
                    "$set": {"updated_at": now},
                },
                return_document=True,
            )
        return {
            "tokens_used": result.get("tokens_used", 0),
            "limit_override": result.get("limit_override"),
        }

    async def reset_user_quota(self, user_id: int) -> None:
        """Zero out a user's usage and restart their window (admin action)."""
        now = datetime.now(timezone.utc)
        await self.user_quotas.update_one(
            {"user_id": user_id},
            {"$set": {"tokens_used": 0, "window_start": now, "updated_at": now}},
            upsert=True,
        )

    async def set_user_quota_limit(self, user_id: int, limit: int) -> None:
        """Set a per-user token limit override. -1 = unlimited, 0 = blocked."""
        now = datetime.now(timezone.utc)
        await self.user_quotas.update_one(
            {"user_id": user_id},
            {
                "$set": {"limit_override": limit, "updated_at": now},
                "$setOnInsert": {"user_id": user_id, "tokens_used": 0, "window_start": now},
            },
            upsert=True,
        )

    async def get_quota_stats(self) -> dict:
        """Return aggregate quota stats for the sysinfo panel."""
        window = self._quota_window_seconds()
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window)
        default_limit = await self.get_setting(
            "ai_quota_token_limit", getattr(Config, "AI_QUOTA_TOKEN_LIMIT", 5000)
        )
        pipeline = [
            {"$match": {"window_start": {"$gte": cutoff}}},
            {"$group": {
                "_id": None,
                "active_users": {"$sum": 1},
                "total_tokens": {"$sum": "$tokens_used"},
                "users_over_limit": {"$sum": {"$cond": [
                    {"$gte": ["$tokens_used", {"$ifNull": ["$limit_override", default_limit]}]},
                    1, 0,
                ]}},
            }},
        ]
        async for doc in self.user_quotas.aggregate(pipeline):
            return {
                "default_limit": default_limit,
                "window_seconds": window,
                "active_users": doc.get("active_users", 0),
                "total_tokens": doc.get("total_tokens", 0),
                "users_over_limit": doc.get("users_over_limit", 0),
            }
        return {
            "default_limit": default_limit,
            "window_seconds": window,
            "active_users": 0,
            "total_tokens": 0,
            "users_over_limit": 0,
        }
