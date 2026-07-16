import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from ladder_utils import parse_rank

async def remove_specified_novices():
    db = Database()
    if not await db.init():
        return
        
    players = await db.player_ranks.find({}).to_list(length=None)
    novices = []
    for p in players:
        rank_str = p.get("rank", "")
        parsed = parse_rank(rank_str)
        if parsed and parsed[0] == "Novices":
            novices.append((p['user_id'], parsed[1]))
            
    novices.sort(key=lambda x: x[1])
    
    # The user wants to remove Novice #4 and beyond.
    # Novice #1 is index 0. Novice #4 is index 3.
    to_remove = novices[3:]
    
    for uid, num in to_remove:
        print(f"Removing Novice #{num} (ID: {uid})...")
        await db.remove_player_from_ladder(uid, is_undo=True)
        
    print("Done removing specified novices!")

if __name__ == "__main__":
    asyncio.run(remove_specified_novices())
