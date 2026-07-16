import asyncio
import os
import sys

# Ensure we can import config and database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

async def restore_novices():
    db = Database()
    if not await db.init():
        print("Failed to init database")
        return
        
    print("Finding recently removed Novices...")
    logs = await db.undo_logs.find({
        "action_type": "remove_player",
        "old_rank": {"$regex": "Novices"}
    }).sort("timestamp", -1).to_list(length=None)
    
    if not logs:
        print("No removed Novices found in the undo logs.")
        return
        
    restored_count = 0
    seen_users = set()
    
    for log in logs:
        target_id = log['target_id']
        
        if target_id in seen_users:
            continue
        seen_users.add(target_id)
        
        # Verify they are currently unranked so we don't overwrite a new rank
        current_player = await db.player_ranks.find_one({"user_id": target_id})
        if current_player and current_player.get("rank"):
            print(f"Skipping {target_id}: They already have a rank ({current_player['rank']})")
            continue
            
        print(f"Restoring {target_id} to {log['old_rank']}...")
        success, msg = await db.undo_last_action(target_id)
        if success:
            restored_count += 1
            print(f"  -> Success: {msg}")
        else:
            print(f"  -> Failed: {msg}")
            
    print(f"\nDone! Successfully restored {restored_count} Novice players.")

if __name__ == "__main__":
    asyncio.run(restore_novices())
