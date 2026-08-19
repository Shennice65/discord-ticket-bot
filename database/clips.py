from datetime import datetime, timezone
from typing import List, Dict

MAX_CLIPS_PER_USER = 10


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

    async def add_user_clip(self, user_id: int, url: str, title: str, thumbnail: str, clip_page_url: str = "") -> bool:
        """Add a clip to a user's collection. Returns False if at max limit."""
        current_count = await self.get_user_clip_count(user_id)
        if current_count >= MAX_CLIPS_PER_USER:
            return False

        clip = {
            "url": url,
            "title": title or "Untitled Clip",
            "thumbnail": thumbnail or "",
            "clip_page_url": clip_page_url or "",
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

    async def toggle_clip_reaction(self, owner_id: int, clip_index: int, user_id: int, reaction_type: str) -> dict:
        """Toggles a reaction and returns the new counts."""
        doc = await self.db.player_clips.find_one({"user_id": owner_id})
        if not doc or clip_index < 0 or clip_index >= len(doc.get("clips", [])):
            return None
            
        clip = doc["clips"][clip_index]
        stars = set(clip.get("stars", []))
        skulls = set(clip.get("skulls", []))
        
        has_starred = user_id in stars
        has_skulled = user_id in skulls
        
        if reaction_type == "star":
            if has_starred:
                stars.remove(user_id)
                has_starred = False
            else:
                stars.add(user_id)
                has_starred = True
        elif reaction_type == "skull":
            if has_skulled:
                skulls.remove(user_id)
                has_skulled = False
            else:
                skulls.add(user_id)
                has_skulled = True
                
        await self.db.player_clips.update_one(
            {"user_id": owner_id},
            {"$set": {
                f"clips.{clip_index}.stars": list(stars),
                f"clips.{clip_index}.skulls": list(skulls)
            }}
        )
        
        return {
            "stars": len(stars), 
            "skulls": len(skulls), 
            "has_starred": has_starred, 
            "has_skulled": has_skulled
        }
