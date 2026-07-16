import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

async def fix_novice_spelling():
    db = Database()
    if not await db.init():
        return
        
    players = await db.player_ranks.find({}).to_list(length=None)
    fixed = 0
    for p in players:
        rank_str = p.get("rank", "")
        if "Novices" in rank_str:
            new_rank = rank_str.replace("Novices", "Novice")
            print(f"Fixing User {p['user_id']}: {rank_str} -> {new_rank}")
            await db.player_ranks.update_one(
                {"user_id": p['user_id']},
                {"$set": {"rank": new_rank}}
            )
            fixed += 1
            
    print(f"Fixed {fixed} players!")

if __name__ == "__main__":
    asyncio.run(fix_novice_spelling())
