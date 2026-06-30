import discord
from discord.ext import commands
import asyncio
import os
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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
        print("Starting setup_hook...")
        # Load cogs
        print("Loading cogs...")
        await self.load_extension("cogs.tickets")
        await self.load_extension("cogs.history")
        await self.load_extension("cogs.ranking")
        print("Cogs loaded. Syncing commands...")
        
        # Sync commands
        try:
            if Config.GUILD_ID:
                guild = discord.Object(id=Config.GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                print(f"Syncing to guild {Config.GUILD_ID}...")
                synced = await self.tree.sync(guild=guild)
                print(f"Synced {len(synced)} commands to guild {Config.GUILD_ID}")
            else:
                print("Syncing globally...")
                synced = await self.tree.sync()
                print(f"Synced {len(synced)} commands globally")
        except Exception as e:
            print(f"Sync error: {e}")
        
        print("setup_hook completed.")
    
    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Bot is in {len(self.guilds)} guilds")
        print("------")
        
    async def on_member_remove(self, member):
        try:
            from database import Database
            db = Database()
            await db.init()
            await db.remove_player_from_ladder(member.id)
            print(f"Removed leaving member {member.name} from ladder.")
        except Exception as e:
            print(f"Error removing member {member.id} from ladder: {e}")
    
    @commands.command(name="sync")
    @commands.has_permissions(administrator=True)
    async def sync_commands(self, ctx):
        """Manually sync slash commands"""
        msg = await ctx.send("Syncing commands...")
        try:
            if Config.GUILD_ID:
                guild = discord.Object(id=Config.GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            await msg.edit(content=f"Synced {len(synced)} commands successfully!")
        except Exception as e:
            await msg.edit(content=f"Error: {e}")

async def main():
    discord.utils.setup_logging()
    keep_alive()
    bot = TicketBot()
    await bot.start(Config.TOKEN)

if __name__ == "__main__":
    asyncio.run(main())