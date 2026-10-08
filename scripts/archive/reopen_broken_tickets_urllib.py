import asyncio
import os
import urllib.request
import urllib.error
import json
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import certifi

load_dotenv()

async def fix_tickets():
    uri = os.environ.get('MONGO_URI') or os.getenv('MONGO_URI')
    token = os.environ.get('DISCORD_TOKEN') or os.getenv('DISCORD_TOKEN')
    
    db = AsyncIOMotorClient(uri, tlsCAFile=certifi.where()).discord_bot_db

    print("Checking for tickets that are closed in the database but the Discord channel still exists...")
    
    # Find all closed tickets
    cursor = db.tickets.find({"status": "closed"})
    closed_tickets = await cursor.to_list(length=None)
    print(f"Found {len(closed_tickets)} closed tickets in total.")
    
    reopened = 0
    
    for t in closed_tickets:
        channel_id = t.get('channel_id')
        
        req = urllib.request.Request(f"https://discord.com/api/v10/channels/{channel_id}")
        req.add_header("Authorization", f"Bot {token}")
        req.add_header("User-Agent", "DiscordBot (ReopenScript, 1.0)")
        
        try:
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    print(f"Found active channel #{data.get('name', 'unknown')} (ID: {channel_id}) but ticket is closed in DB! Reopening...")
                    await db.tickets.update_one(
                        {"channel_id": channel_id},
                        {"$set": {"status": "open", "closed_at": None, "closed_by": None}}
                    )
                    reopened += 1
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Channel deleted, this is correct for a closed ticket
                pass
            else:
                print(f"Unexpected HTTPError for {channel_id}: {e.code}")
        except Exception as e:
            print(f"Error checking {channel_id}: {e}")
            
    print(f"\nDone! Reopened {reopened} ticket(s) that were broken by the bug.")

if __name__ == "__main__":
    asyncio.run(fix_tickets())
