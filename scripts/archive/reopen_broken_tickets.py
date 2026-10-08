import asyncio
import os
import discord
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import certifi

load_dotenv()

class FixBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        
    async def setup_hook(self):
        uri = os.environ.get('MONGO_URI') or os.getenv('MONGO_URI')
        self.db = AsyncIOMotorClient(uri, tlsCAFile=certifi.where()).discord_bot_db

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        print("Checking for tickets that are closed in the database but the Discord channel still exists...")
        
        # Find all closed tickets
        cursor = self.db.tickets.find({"status": "closed"})
        closed_tickets = await cursor.to_list(length=None)
        
        reopened = 0
        for t in closed_tickets:
            channel_id = t.get('channel_id')
            try:
                # If we can fetch the channel, it still exists in Discord!
                channel = await self.fetch_channel(channel_id)
                if channel:
                    print(f"Found active channel #{channel.name} (ID: {channel_id}) but ticket is closed in DB! Reopening...")
                    await self.db.tickets.update_one(
                        {"channel_id": channel_id},
                        {"$set": {"status": "open", "closed_at": None, "closed_by": None}}
                    )
                    reopened += 1
            except discord.NotFound:
                # Channel is actually deleted, which is correct for a closed ticket
                pass
            except discord.Forbidden:
                pass
            except Exception as e:
                print(f"Error checking {channel_id}: {e}")
                
        print(f"\nDone! Reopened {reopened} ticket(s) that were broken by the bug.")
        await self.close()

if __name__ == "__main__":
    bot = FixBot()
    bot.run(os.environ.get('DISCORD_TOKEN'))
