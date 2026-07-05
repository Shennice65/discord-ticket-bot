import sys
import os
import asyncio

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

async def main():
    db = Database()
    if not await db.init():
        print("Failed to init database")
        return

    user_id = 442188857014747136
    
    # Try to find a result where this user was the winner
    result = await db.ranked_results.find_one({"winner_id": user_id})
    if result:
        print(f"User is: {result.get('winner')}")
        return
        
    # If not found, try to look at ticket opponent names
    ticket = await db.tickets.find_one({"opponent_id": user_id})
    if ticket:
        print(f"User is: {ticket.get('opponent_name')}")
        return
        
    print("Could not find a name for this user in recent matches.")

if __name__ == "__main__":
    asyncio.run(main())
