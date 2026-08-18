from datetime import datetime, timezone
from typing import List, Dict

MAX_CLIPS_PER_USER = 5


class ClipsMixin:
    async def get_user_clips(self, user_id: int) -> List[Dict]:
        """Returns the list of clips for a user, or empty list if none."""
        doc = await self.db.player_clips.find_one({"user_id": user_id})
        if not doc:
            return []
        return doc.get("clips", [])

    async def get_user_clip_count(self, user_id: int) -> int:
        """Returns the number of clips a user has stored."""
        doc = await self.db.player_clips.find_one({"user_id": user_id}, {"clips": 1})
        if not doc:
            return 0
        return len(doc.get("clips", []))

    async def add_user_clip(self, user_id: int, url: str, title: str, thumbnail: str) -> bool:
        """Add a clip to a user's collection. Returns False if at max limit."""
        current_count = await self.get_user_clip_count(user_id)
        if current_count >= MAX_CLIPS_PER_USER:
            return False

        clip = {
            "url": url,
            "title": title or "Untitled Clip",
            "thumbnail": thumbnail or "",
            "submitted_at": datetime.now(timezone.utc).isoformat()
        }

        await self.db.player_clips.update_one(
            {"user_id": user_id},
            {"$push": {"clips": clip}},
            upsert=True
        )
        return True

    async def delete_user_clip(self, user_id: int, clip_index: int) -> bool:
        """Delete a clip by its 0-based index. Returns False if index is invalid."""
        doc = await self.db.player_clips.find_one({"user_id": user_id})
        if not doc:
            return False

        clips = doc.get("clips", [])
        if clip_index < 0 or clip_index >= len(clips):
            return False

        clips.pop(clip_index)
        await self.db.player_clips.update_one(
            {"user_id": user_id},
            {"$set": {"clips": clips}}
        )
        return True
