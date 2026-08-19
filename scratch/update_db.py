import asyncio
import os
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

async def update_db():
    print(f"Connecting to: {Config.MONGO_URI}")
    client = AsyncIOMotorClient(Config.MONGO_URI, tlsCAFile=certifi.where())
    db = client.discord_bot_db
    config_col = db.config
    
    await config_col.update_one(
        {"_id": "api_keys"},
        {
            "$set": {
                "CLIPS_SERVICE_URL": "https://clip-hosting-o361.onrender.com",
                "CLIPS_ADMIN_PASSWORD": "1234566Aa@"
            }
        },
        upsert=True
    )
    print("Database updated!")

if __name__ == "__main__":
    asyncio.run(update_db())
