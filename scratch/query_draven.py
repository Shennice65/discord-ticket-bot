import asyncio
import os
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

async def query_db():
    print(f"Connecting to: {Config.MONGO_URI}")
    client = AsyncIOMotorClient(Config.MONGO_URI, tlsCAFile=certifi.where())
    db = client.discord_bot_db

    collections = await db.list_collection_names()
    print(f"Collections: {collections}")

    search_terms = ["9764485573", 9764485573, "drxzvn", "Draven"]

    for coll_name in collections:
        coll = db[coll_name]
        print(f"\n--- Searching in {coll_name} ---")

        for term in search_terms:
            # Search common fields
            queries = [
                {"_id": term},
                {"roblox_id": term},
                {"discord_id": term},
                {"username": term},
                {"discord_username": term}
            ]

            for query in queries:
                try:
                    results = await coll.find(query).to_list(length=10)
                    if results:
                        print(f"Found matches for query {query}:")
                        for res in results:
                            print(res)
                except Exception as e:
                    pass

if __name__ == "__main__":
    asyncio.run(query_db())
