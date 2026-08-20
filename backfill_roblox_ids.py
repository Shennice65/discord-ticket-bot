import os
import certifi
import json
import urllib.request
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv('d:/coding/discord-bot/.env')
MONGO_URI = os.environ.get('MONGO_URI')

# Connect to database
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client.discord_bot_db
collection = db.roblox_usernames

# Find all users missing a roblox_id
users_missing_id = list(collection.find({
    "$or": [
        {"roblox_id": {"$exists": False}},
        {"roblox_id": None},
        {"roblox_id": ""}
    ]
}))

print(f"Found {len(users_missing_id)} users missing roblox_id.")

if not users_missing_id:
    print("Nothing to do.")
    exit(0)

# Extract usernames
# We only want to query users that actually have a username set
users_with_name = [u for u in users_missing_id if u.get("username")]
usernames_to_fetch = [u["username"] for u in users_with_name]

print(f"Fetching IDs for {len(usernames_to_fetch)} usernames from Roblox...")

def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

# Roblox API allows up to 100 usernames per request
updated_count = 0
for chunk in chunk_list(usernames_to_fetch, 100):
    try:
        req_data = json.dumps({
            "usernames": chunk,
            "excludeBannedUsers": False
        }).encode('utf-8')
        
        req = urllib.request.Request(
            'https://users.roblox.com/v1/usernames/users',
            data=req_data,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0'
            }
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                
                # Update database
                for item in data.get("data", []):
                    username = item.get("requestedUsername")
                    roblox_id = str(item.get("id"))
                    
                    if username and roblox_id:
                        result = collection.update_many(
                            {"username": {"$regex": f"^{username}$", "$options": "i"}},
                            {"$set": {"roblox_id": roblox_id}}
                        )
                        updated_count += result.modified_count
                        
    except Exception as e:
        print(f"Error fetching chunk {chunk}: {e}")

print(f"Successfully backfilled roblox_id for {updated_count} players!")
