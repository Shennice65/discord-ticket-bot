import asyncio
import os
import requests
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
    
    reopened = 0
    headers = {"Authorization": f"Bot {token}"}
    
    for t in closed_tickets:
        channel_id = t.get('channel_id')
        
        # Check if channel exists via Discord REST API
        url = f"https://discord.com/api/v10/channels/{channel_id}"
        resp = requests.get(url, headers=headers)
        
        if resp.status_code == 200:
            channel_data = resp.json()
            print(f"Found active channel #{channel_data.get('name', 'unknown')} (ID: {channel_id}) but ticket is closed in DB! Reopening...")
            await db.tickets.update_one(
                {"channel_id": channel_id},
                {"$set": {"status": "open", "closed_at": None, "closed_by": None}}
            )
            reopened += 1
        elif resp.status_code == 404:
            # Channel deleted, this is correct for a closed ticket
            pass
        else:
            print(f"Unexpected response checking {channel_id}: {resp.status_code} - {resp.text}")
            
    print(f"\nDone! Reopened {reopened} ticket(s) that were broken by the bug.")

if __name__ == "__main__":
    asyncio.run(fix_tickets())
