"""Irreversibly clear only the bot's AI memory collections.

Usage:
    python scripts/reset_ai_memory.py --confirm-ai-reset
"""

import argparse
import asyncio

import certifi
from motor.motor_asyncio import AsyncIOMotorClient

from config import Config


COLLECTIONS = ("chat_memory", "chat_messages", "pending_lore")


async def clear_ai_collections(database):
    counts = {
        name: await database[name].count_documents({})
        for name in COLLECTIONS
    }
    for name in COLLECTIONS:
        await database[name].delete_many({})
    remaining = {
        name: await database[name].count_documents({})
        for name in COLLECTIONS
    }
    return counts, remaining


async def reset():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-ai-reset",
        action="store_true",
        help="Confirm irreversible deletion of the three AI collections.",
    )
    args = parser.parse_args()
    if not args.confirm_ai_reset:
        parser.error("refusing to delete anything without --confirm-ai-reset")
    if not Config.MONGO_URI:
        raise RuntimeError("MONGO_URI is not configured")

    client = AsyncIOMotorClient(Config.MONGO_URI, tlsCAFile=certifi.where())
    try:
        database = client[Config.MONGO_DB_NAME]
        counts, remaining = await clear_ai_collections(database)
        print("AI collection counts before reset:", counts)
        print("AI collection counts after reset:", remaining)
        if any(remaining.values()):
            raise RuntimeError("reset completed with remaining AI records")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(reset())
