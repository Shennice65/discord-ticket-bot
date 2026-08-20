"""
Migration script - MongoDB only.
Updates all clip URLs to use the new atlclips.site domain.
R2 files keep their original names (old long-hash URLs still resolve via the /clip/ route).
"""
import os
import re
import asyncio
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")
OLD_DOMAIN = "clip-hosting-o361.onrender.com"
NEW_DOMAIN = "atlclips.site"

async def migrate():
    print("=" * 60)
    print("CLIP MIGRATION: Update MongoDB URLs to new domain")
    print("=" * 60)
    
    client = AsyncIOMotorClient(MONGO_URI, tlsCAFile=certifi.where())
    db = client.discord_bot_db
    
    updated_users = 0
    updated_clips = 0
    
    async for player_doc in db.player_clips.find({}):
        user_id = player_doc.get("user_id")
        clips = player_doc.get("clips", [])
        changed = False
        
        for clip in clips:
            clip_page_url = clip.get("clip_page_url", "")
            
            if clip_page_url and OLD_DOMAIN in clip_page_url:
                new_url = clip_page_url.replace(OLD_DOMAIN, NEW_DOMAIN)
                print(f"  User {user_id}:")
                print(f"    OLD: {clip_page_url}")
                print(f"    NEW: {new_url}")
                clip["clip_page_url"] = new_url
                changed = True
                updated_clips += 1
        
        if changed:
            await db.player_clips.update_one(
                {"user_id": user_id},
                {"$set": {"clips": clips}}
            )
            updated_users += 1
    
    client.close()
    
    print("")
    print("=" * 60)
    print("MIGRATION COMPLETE")
    print(f"  MongoDB users updated: {updated_users}")
    print(f"  MongoDB clips updated: {updated_clips}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(migrate())
