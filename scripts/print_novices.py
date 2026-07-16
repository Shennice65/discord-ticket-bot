import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from ladder_utils import parse_rank

async def print_novices():
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
    for uid, num in novices:
        print(f"Novices {num}: {uid}")

if __name__ == "__main__":
    asyncio.run(print_novices())
