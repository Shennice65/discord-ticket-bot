import asyncio
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import certifi

load_dotenv()

async def fix_tickets():
    uri = os.environ.get('MONGO_URI') or os.getenv('MONGO_URI')
    db = AsyncIOMotorClient(uri, tlsCAFile=certifi.where()).discord_bot_db

    print("Reopening tickets closed in the last 48 hours...")
    
    cursor = db.tickets.find({"status": "closed"})
    closed_tickets = await cursor.to_list(length=None)
    
    cutoff = datetime.utcnow() - timedelta(hours=48)
    reopened = 0
    
    for t in closed_tickets:
        closed_at_str = t.get('closed_at')
        if closed_at_str:
            try:
                closed_at = datetime.fromisoformat(closed_at_str)
                if closed_at > cutoff:
                    await db.tickets.update_one(
                        {"channel_id": t['channel_id']},
                        {"$set": {"status": "open", "closed_at": None, "closed_by": None}}
                    )
                    reopened += 1
            except ValueError:
                pass
                
    print(f"\nDone! Reopened {reopened} tickets that were closed in the last 48 hours.")

if __name__ == "__main__":
    asyncio.run(fix_tickets())
