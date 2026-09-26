import discord
from discord.ext import commands, tasks
from discord import app_commands
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
from web.dashboard import start_web_server
from framework.plugins import PluginRegistry


class BotCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        blocked_user_ids = await self.client.db.get_setting("blocked_user_ids", [])
        if interaction.user.id not in blocked_user_ids:
            return True

        await interaction.response.send_message(
            "next time dont mess with the big boss fool",
            ephemeral=True,
        )
        return False

class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            tree_cls=BotCommandTree,
        )
        
        self.db = Database()
        self.plugin_registry = PluginRegistry()
        
        self.container = Container()
        self.container.register('Database', self.db)
        self.container.register('RankingService', RankingService(self, self.db))
        self.container.register('TicketService', TicketService(self, self.db))
    
    async def setup_hook(self):
        print("Starting setup_hook...")
        if not await self.db.init():
            raise RuntimeError("MongoDB initialization failed; refusing to start the bot")

        loaded_plugins = self.plugin_registry.load_directory()
        print(f"Loaded {loaded_plugins} bot plugin(s).")
        
        # Initialize persistent view components.
        from views.history_views import ShareClipView
        self.add_view(ShareClipView())
        
        print("Loading cogs...")
        await self.load_extension("cogs.chat")
        await self.load_extension("cogs.tickets.core")
        await self.load_extension("cogs.tickets.admin")
        await self.load_extension("cogs.tickets.tasks")
        await self.load_extension("cogs.ranking.history")
        await self.load_extension("cogs.ranking.core")
        await self.load_extension("cogs.ranking.admin")
        await self.load_extension("cogs.ranking.cooldowns")
        await self.load_extension("cogs.owner")
        await self.load_extension("cogs.betting")
        await self.load_extension("cogs.activity")
        await self.load_extension("cogs.engagement_cmds")
        print("Cogs loaded. Syncing commands...")
        
        port = int(os.environ.get("PORT", 8080))
        asyncio.create_task(start_web_server(self, port=port))
        
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

    async def close(self):
        chat = self.get_cog("Chat")
        sidecar = getattr(getattr(chat, "router", None), "sidecar", None)
        if sidecar is not None:
            await sidecar.stop()
        await super().close()
        
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
        if not Config.GUILD_ID or member.guild.id != Config.GUILD_ID:
            return
        try:
            await self.db.remove_player_from_ladder(member.id)
            print(f"Removed leaving member {member.name} from ladder.")
        except Exception as e:
            print(f"Error removing member {member.id} from ladder: {e}")
    
    @commands.command(name="reload")
    @commands.has_permissions(administrator=True)
    async def reload_cog(self, ctx, extension: str):
        try:
            await self.reload_extension(extension)
            
            import subprocess
            file_path = extension.replace('.', '/') + '.py'
            try:
                result = subprocess.run(['git', 'log', '-1', '--pretty=format:%s', '--', file_path], 
                                      capture_output=True, text=True, check=True)
                last_change = result.stdout.strip()
                if last_change:
                    await ctx.send(f"✅ Successfully reloaded `{extension}`\n**Latest Change:** {last_change}")
                else:
                    await ctx.send(f"✅ Successfully reloaded `{extension}`")
            except Exception:
                await ctx.send(f"✅ Successfully reloaded `{extension}`")
                
        except Exception as e:
            await ctx.send(f"❌ Failed to reload `{extension}`: {e}")

    @reload_cog.error
    async def reload_cog_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You need Administrator permissions to reload cogs!")
        else:
            await ctx.send(f"❌ Error: {error}")

    @commands.command(name="restart")
    @commands.has_permissions(administrator=True)
    async def restart_bot(self, ctx):
        await ctx.send("Restarting bot...")
        import sys
        sys.exit(0)

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
    # Ensure aiohttp's Discord session is closed if startup fails before login
    # completes (for example, when DNS or the network is temporarily down).
    async with bot:
        await bot.start(Config.TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
