import os
import certifi
import json
import urllib.request
import time
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv('../.env')
MONGO_URI = os.environ.get('MONGO_URI')

# Connect to database
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client.discord_bot_db
roblox_col = db.roblox_usernames

# Find all cached roblox users missing display_name
users = list(roblox_col.find({"display_name": {"$exists": False}}))
print(f"Found {len(users)} roblox users missing display_name.")

if not users:
    exit(0)

# Batch into chunks of 100 for Roblox bulk API
chunk_size = 100
for i in range(0, len(users), chunk_size):
    chunk = users[i:i+chunk_size]
    roblox_ids = [u["roblox_id"] for u in chunk if "roblox_id" in u]
    if not roblox_ids:
        continue
        
    try:
        data = json.dumps({"userIds": roblox_ids}).encode('utf-8')
        req = urllib.request.Request(
            'https://users.roblox.com/v1/users',
            data=data,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0'
            },
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                resp_data = json.loads(response.read().decode())
                
                # Create map of roblox_id -> display_name
                id_to_display = {}
                for item in resp_data.get("data", []):
                    id_to_display[str(item["id"])] = item.get("displayName")
                    
                # Update DB
                for u in chunk:
                    r_id = str(u.get("roblox_id"))
                    display_name = id_to_display.get(r_id) or u.get("username")
                    roblox_col.update_one(
                        {"_id": u["_id"]},
                        {"$set": {"display_name": display_name}}
                    )
                
                print(f"Updated {len(chunk)} users.")
                
        time.sleep(0.5)
        
    except Exception as e:
        print(f"Error fetching chunk: {e}")
        
print("Backfill complete.")
