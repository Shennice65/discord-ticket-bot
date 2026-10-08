import asyncio
import os
import sys
import discord

# Ensure we can import config and database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

async def cleanup_dummies():
    db = Database()
    if not await db.init():
        print("Failed to init database")
        return
        
    dummy_start_id = 900000000000000000
    for i in range(8):
        user_id = dummy_start_id + i
        print(f"Removing dummy user: {user_id}")
        await db.remove_player_from_ladder(user_id, is_undo=True)
        
    print("Cleanup complete!")

if __name__ == "__main__":
    asyncio.run(cleanup_dummies())
