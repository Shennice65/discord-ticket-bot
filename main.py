import discord
from discord.ext import commands
import asyncio
import os

from config import Config
from keep_alive import keep_alive

class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
    
    async def setup_hook(self):
        # Load cogs
        await self.load_extension("cogs.tickets")
        await self.load_extension("cogs.history")
        
        # Sync commands
        try:
            if Config.GUILD_ID:
                guild = discord.Object(id=Config.GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"✅ Synced {len(synced)} commands to guild {Config.GUILD_ID}")
            else:
                synced = await self.tree.sync()
                print(f"✅ Synced {len(synced)} commands globally (may take up to 1 hour)")
        except Exception as e:
            print(f"❌ Sync error: {e}")
    
    async def on_ready(self):
        print(f"✅ Logged in as {self.user} (ID: {self.user.id})")
        print(f"✅ Bot is in {len(self.guilds)} guilds")
        print("------")

async def main():
    # Start the keep-alive web server FIRST
    keep_alive()
    
    # Then start the bot
    bot = TicketBot()
    await bot.start(Config.TOKEN)

if __name__ == "__main__":
    asyncio.run(main())