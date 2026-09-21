import asyncio
import os
import sys
sys.path.append('.')
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

async def drop_memories():
    print("Connecting to DB...")
    client = AsyncIOMotorClient(Config.MONGO_URI)
    db = client[Config.MONGO_DB_NAME]
    
    print("Dropping chat_memory collection...")
    await db.drop_collection("chat_memory")
    
    # Also drop pending_lore just to be completely fresh for the new pipeline
    print("Dropping pending_lore collection...")
    await db.drop_collection("pending_lore")
    print("Done!")

if __name__ == "__main__":
    asyncio.run(drop_memories())
