import asyncio
import os
import sys
from datetime import datetime

# Add parent directory to path so we can import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
import certifi

async def main():
    if not Config.MONGO_URI:
        print("MONGO_URI not found in config!")
        return

    client = AsyncIOMotorClient(Config.MONGO_URI, tlsCAFile=certifi.where())
    db = client.discord_bot_db
    tickets = db.tickets
    ranked_results = db.ranked_results

    # Find tickets that were backdated to 2000-01-01 00:00:00
    bad_date_str = str(datetime(2000, 1, 1))
    
    cursor = tickets.find({"closed_at": bad_date_str})
    bad_tickets = await cursor.to_list(length=None)
    
    print(f"Found {len(bad_tickets)} tickets with 2000-01-01 date.")
    
    fixed_count = 0
    for ticket in bad_tickets:
        ticket_id = ticket["id"]
        
        # Try to find corresponding result
        result = await ranked_results.find_one({"ticket_id": ticket_id})
        
        if result and "created_at" in result:
            correct_date = result["created_at"]
        else:
            # Fallback to the ticket's creation date
            correct_date = ticket["created_at"]
            
        await tickets.update_one(
            {"_id": ticket["_id"]},
            {"$set": {"closed_at": correct_date}}
        )
        print(f"Ticket {ticket_id}: restored closed_at to {correct_date}")
        fixed_count += 1
        
    print(f"\nMigration complete. Fixed {fixed_count} tickets.")

if __name__ == "__main__":
    asyncio.run(main())
