import sys
import os
import asyncio

# Add the parent directory to sys.path so we can import config and database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

async def main():
    db = Database()
    if not await db.init():
        print("Failed to init database")
        return

    print("Fetching all players...")
    players = await db.get_all_player_ranks()
    print(f"Found {len(players)} players.")

    for player in players:
        user_id = player["user_id"]
        
        # Get all closed ranked tickets involving this user, sorted by newest first
        pipeline = [
            {"$match": {
                "status": "closed",
                "ticket_type": "Ranked 1v1",
                "$or": [{"user_id": user_id}, {"opponent_id": user_id}]
            }},
            {"$sort": {"closed_at": -1}},
            {"$lookup": {
                "from": "ranked_results",
                "localField": "id",
                "foreignField": "ticket_id",
                "as": "result"
            }},
            {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}}
        ]
        
        cursor = db.tickets.aggregate(pipeline)
        matches = await cursor.to_list(length=None)
        
        streak = 0
        for match in matches:
            # If the user won, increment streak
            if match["result"].get("winner_id") == user_id:
                streak += 1
            else:
                # User lost, streak is broken
                break
                
        # Update the player's document
        await db.player_ranks.update_one(
            {"user_id": user_id},
            {"$set": {"win_streak": streak}}
        )
        print(f"User {user_id} -> streak {streak}")

    print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(main())
