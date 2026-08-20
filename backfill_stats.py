import os
import certifi
from pymongo import MongoClient
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(env_path)
MONGO_URI = os.environ.get("MONGO_URI")

client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client.discord_bot_db

print("Starting backfill process...")

players = list(db.player_ranks.find({}))
print(f"Found {len(players)} players in player_ranks.")

updated_count = 0
for p in players:
    user_id = p["user_id"]
    
    pipeline = [
        {"$match": {"$or": [{"user_id": user_id}, {"opponent_id": user_id}], "status": "closed"}},
        {"$lookup": {
            "from": "ranked_results",
            "localField": "id",
            "foreignField": "ticket_id",
            "as": "result"
        }},
        {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}},
        {"$project": {
            "winner_id": "$result.winner_id"
        }}
    ]
    
    matches = list(db.tickets.aggregate(pipeline))
    total_matches = len(matches)
    
    wins = 0
    for m in matches:
        if m.get("winner_id") == user_id:
            wins += 1
            
    losses = total_matches - wins
    
    db.player_ranks.update_one(
        {"_id": p["_id"]},
        {"$set": {"wins": wins, "losses": losses, "matches": total_matches}}
    )
    
    updated_count += 1
    
print(f"Successfully backfilled stats for {updated_count} players.")
