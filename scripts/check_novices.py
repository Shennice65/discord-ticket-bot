import asyncio
import os
import sys

# Ensure we can import config and database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from datetime import datetime

async def check_novices():
    db = Database()
    if not await db.init():
        print("Failed to init database")
        return
        
    print("--- CURRENT NOVICES ---")
    current_players = await db.player_ranks.find({}).to_list(length=None)
    novice_count = 0
    for p in current_players:
        if "Novice" in p.get("rank", ""):
            print(f"User {p['user_id']} is currently {p['rank']}")
            novice_count += 1
    
    if novice_count == 0:
        print("No current Novices found.")
        
    print("\n--- NOVICE HISTORY IN UNDO LOGS ---")
    logs = await db.undo_logs.find({
        "$or": [
            {"old_rank": {"$regex": "Novice"}},
            {"new_rank": {"$regex": "Novice"}}
        ]
    }).sort("timestamp", -1).limit(20).to_list(length=20)
    
    if not logs:
        print("No recent Novice rank changes found in undo logs.")
    else:
        for log in logs:
            print(f"[{log['timestamp']}] User {log['target_id']}: {log.get('old_rank', 'Unranked')} -> {log.get('new_rank', 'Unranked')} (Action: {log['action_type']})")
            
    print("\n--- PREVIOUSLY RANKED AS NOVICE (Original Rank) ---")
    original_novices = 0
    for p in current_players:
        if "Novice" in p.get("original_rank", ""):
            print(f"User {p['user_id']} was originally {p['original_rank']} (Currently {p.get('rank', 'Unranked')})")
            original_novices += 1
            
    if original_novices == 0:
        print("No players found with original_rank as Novice.")

if __name__ == "__main__":
    asyncio.run(check_novices())
