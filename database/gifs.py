"""GIF library CRUD for community-learned GIF responses."""

import logging
import random
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Valid context tags the system recognizes.
VALID_CONTEXT_TAGS = frozenset({
    "roast", "hype", "sadness", "laugh", "win", "loss",
    "reaction", "greeting", "flex", "confused", "cringe",
})


def _is_gif_url(url: str) -> bool:
    """Basic validation that a URL looks like a GIF or animated image."""
    lower = (url or "").strip().lower()
    return any(
        domain in lower
        for domain in ("tenor.com", "giphy.com", "cdn.discordapp.com", "media.discordapp.net")
    ) or lower.endswith((".gif", ".gifv"))


class GifMixin:
    async def record_gif(
        self,
        guild_id: int,
        url: str,
        context_tag: str,
        taught_by: int | None = None,
        source_message_id: int | None = None,
    ) -> bool:
        """Upsert a GIF into the library. Returns True if newly inserted."""
        if self.gif_library is None:
            return False
        url = url.strip()
        if not _is_gif_url(url):
            return False
        context_tag = context_tag.strip().lower()
        if context_tag not in VALID_CONTEXT_TAGS:
            return False

        now = datetime.now(timezone.utc)
        update: dict = {
            "$setOnInsert": {
                "guild_id": guild_id,
                "url": url,
                "usage_count": 0,
                "community_count": 0,
                "first_seen": now,
            },
            "$set": {"last_used": now},
            "$addToSet": {"context_tags": context_tag},
        }
        if taught_by is not None:
            update["$set"]["taught_by"] = taught_by
        if source_message_id is not None:
            update["$addToSet"]["source_message_ids"] = source_message_id
        update.setdefault("$inc", {})["community_count"] = 1

        try:
            result = await self.gif_library.update_one(
                {"guild_id": guild_id, "url": url},
                update,
                upsert=True,
            )
            return result.upserted_id is not None
        except Exception as error:
            logger.warning("GIF record failed url=%s error=%s", url[:80], type(error).__name__)
            return False

    async def get_gifs_by_context(self, guild_id: int, context_tag: str, limit: int = 5) -> list:
        """Return top GIFs for a context tag, weighted by community usage."""
        if self.gif_library is None:
            return []
        try:
            cursor = self.gif_library.find(
                {"guild_id": guild_id, "context_tags": context_tag.lower()},
            ).sort([("community_count", -1), ("usage_count", -1)]).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as error:
            logger.warning("GIF lookup failed tag=%s error=%s", context_tag, type(error).__name__)
            return []

    async def get_random_gif(self, guild_id: int, context_tag: str) -> dict | None:
        """Return a weighted-random GIF for a context tag."""
        gifs = await self.get_gifs_by_context(guild_id, context_tag, limit=10)
        if not gifs:
            return None
        # Weight by community_count so popular GIFs appear more often.
        weights = [max(1, g.get("community_count", 1)) for g in gifs]
        return random.choices(gifs, weights=weights, k=1)[0]

    async def increment_gif_usage(self, guild_id: int, url: str) -> None:
        """Track that the bot used this GIF in a response."""
        if self.gif_library is None:
            return
        try:
            await self.gif_library.update_one(
                {"guild_id": guild_id, "url": url},
                {"$inc": {"usage_count": 1}, "$set": {"last_used": datetime.now(timezone.utc)}},
            )
        except Exception as error:
            logger.debug("GIF usage increment failed error=%s", type(error).__name__)

    async def get_gif_stats(self, guild_id: int) -> dict:
        """Return aggregate stats for the guild's GIF library."""
        if self.gif_library is None:
            return {"total": 0, "tags": {}}
        try:
            total = await self.gif_library.count_documents({"guild_id": guild_id})
            pipeline = [
                {"$match": {"guild_id": guild_id}},
                {"$unwind": "$context_tags"},
                {"$group": {"_id": "$context_tags", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
            tag_counts = {}
            async for doc in self.gif_library.aggregate(pipeline):
                tag_counts[doc["_id"]] = doc["count"]
            return {"total": total, "tags": tag_counts}
        except Exception as error:
            logger.warning("GIF stats failed error=%s", type(error).__name__)
            return {"total": 0, "tags": {}}

    async def remove_gif(self, guild_id: int, url: str) -> bool:
        """Remove a GIF from the library. Returns True if deleted."""
        if self.gif_library is None:
            return False
        try:
            result = await self.gif_library.delete_one({"guild_id": guild_id, "url": url})
            return result.deleted_count > 0
        except Exception as error:
            logger.warning("GIF removal failed error=%s", type(error).__name__)
            return False
