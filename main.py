import discord
from discord.ext import commands, tasks
import asyncio
import os
import sys
import aiohttp

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from config import Config
from utils.keep_alive import keep_alive
from database import Database
from core.container import Container
from core.services.ranking_service import RankingService
from core.services.ticket_service import TicketService

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
        
        self.db = Database()
        
        self.container = Container()
        self.container.register('Database', self.db)
        self.container.register('RankingService', RankingService(self, self.db))
        self.container.register('TicketService', TicketService(self, self.db))
    
    async def setup_hook(self):
        print("Starting setup_hook...")
        if not await self.db.init():
            raise RuntimeError("MongoDB initialization failed; refusing to start the bot")
        
        # Register persistent views so buttons on old messages still work after restart
        from views.history_views import ShareClipView
        self.add_view(ShareClipView())
        
        print("Loading cogs...")
        await self.load_extension("cogs.tickets.core")
        await self.load_extension("cogs.tickets.admin")
        await self.load_extension("cogs.tickets.tasks")
        await self.load_extension("cogs.ranking.history")
        await self.load_extension("cogs.ranking.core")
        await self.load_extension("cogs.ranking.admin")
        await self.load_extension("cogs.ranking.cooldowns")
        await self.load_extension("cogs.owner")
        await self.load_extension("cogs.betting")
        print("Cogs loaded. Syncing commands...")
        
        self.ping_clips_service.start()
        
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
        
    @tasks.loop(minutes=14)
    async def ping_clips_service(self):
        url = Config.CLIPS_SERVICE_URL
        if getattr(self, 'db', None) and getattr(self.db, 'db', None) is not None:
            try:
                config_doc = await self.db.db.config.find_one({"_id": "api_keys"})
                if config_doc and config_doc.get("CLIPS_SERVICE_URL"):
                    url = config_doc.get("CLIPS_SERVICE_URL")
            except Exception:
                pass
                
        if url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=60) as resp:
                        pass
            except asyncio.TimeoutError:
                print("Ping to clips service timed out (Render is likely waking it up).")
            except Exception as e:
                print(f"Failed to ping clips service: {e}")
                
    @ping_clips_service.before_loop
    async def before_ping_clips_service(self):
        await self.wait_until_ready()

    async def on_member_remove(self, member):
        try:
            await self.db.remove_player_from_ladder(member.id)
            print(f"Removed leaving member {member.name} from ladder.")
        except Exception as e:
            print(f"Error removing member {member.id} from ladder: {e}")
    
    @commands.command(name="sync")
    @commands.has_permissions(administrator=True)
    async def sync_commands(self, ctx, option: str = None):
        msg = await ctx.send("Syncing commands...")
        try:
            if option == "clear":
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
                await msg.edit(content="Cleared global commands! (Old deleted commands will now disappear)")
            elif Config.GUILD_ID:
                guild = discord.Object(id=Config.GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                await msg.edit(content=f"Synced {len(synced)} commands to guild {Config.GUILD_ID}!")
            else:
                synced = await self.tree.sync()
                await msg.edit(content=f"Synced {len(synced)} commands globally!")
        except Exception as e:
            await msg.edit(content=f"Error: {e}")

async def main():
    discord.utils.setup_logging()
    keep_alive()
    bot = TicketBot()
    await bot.start(Config.TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
