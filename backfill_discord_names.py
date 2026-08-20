import os
import certifi
import json
import urllib.request
import time
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv('d:/coding/discord-bot/.env')
MONGO_URI = os.environ.get('MONGO_URI')
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')

# Connect to database
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client.discord_bot_db
ranks_col = db.player_ranks

# Find all players to backfill both username and display name
players = list(ranks_col.find({}))

print(f"Found {len(players)} players to update in player_ranks.")

updated_count = 0
for i, player in enumerate(players):
    user_id = player['user_id']
    
    try:
        req = urllib.request.Request(
            f'https://discord.com/api/v10/users/{user_id}',
            headers={
                'Authorization': f'Bot {DISCORD_TOKEN}',
                'User-Agent': 'DiscordBot (https://example.com, 1.0)'
            }
        )
        
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                username = data.get('username')
                display_name = data.get('global_name') or username
                
                if username:
                    ranks_col.update_one(
                        {"_id": player["_id"]},
                        {"$set": {
                            "discord_username": username,
                            "discord_display_name": display_name
                        }}
                    )
                    updated_count += 1
                    print(f"Updated {user_id}")
                
        # Discord rate limits (50 requests per second is the limit, but we sleep to be safe)
        time.sleep(0.1)
        
    except Exception as e:
        print(f"Error fetching user {user_id}: {e}")
        time.sleep(1) # Backoff on error

print(f"Successfully backfilled {updated_count} Discord usernames!")
