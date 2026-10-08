import asyncio
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import certifi

load_dotenv()

async def fix_stuck_tickets():
    uri = os.environ.get('MONGO_URI') or os.getenv('MONGO_URI')
    client = AsyncIOMotorClient(uri, tlsCAFile=certifi.where())
    db = client.discord_bot_db
    
    # Find all tickets stuck in "processing"
    stuck = await db.tickets.find({"status": "processing"}).to_list(length=None)
    
    if not stuck:
        print("No stuck tickets found!")
        return
    
    print(f"Found {len(stuck)} stuck ticket(s):")
    for t in stuck:
        print(f"  - Ticket #{t.get('id')} | Channel: {t.get('channel_id')} | Type: {t.get('ticket_type')}")
    
    # Reset them all back to "open"
    result = await db.tickets.update_many(
        {"status": "processing"},
        {"$set": {"status": "open"}}
    )
    print(f"\nFixed {result.modified_count} ticket(s) -> status set back to 'open'")

asyncio.run(fix_stuck_tickets())
