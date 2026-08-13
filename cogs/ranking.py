import discord
from discord.ext import commands
from discord import app_commands
import re
from typing import List, Optional
from datetime import datetime

from database import Database
from config import Config

TIERS = ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]

from views.ranking_views import *
from utils.ranking_utils import *

class Ranking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._panel_task = None
        
    @commands.Cog.listener()
    async def on_ready(self):
        print("Ranking cog loaded")
        # Persistent component views across bot reboots
        self.bot.add_view(RankingPaginationView())
        self.bot.add_view(LeaderboardLauncherView())
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not Config.RANKING_PANEL_CHANNEL_ID or message.channel.id != Config.RANKING_PANEL_CHANNEL_ID:
            return
            
        if message.author == self.bot.user and message.embeds and message.embeds[0].title == "🏆 Server Leaderboard":
            return
            
        if self._panel_task and not self._panel_task.done():
            self._panel_task.cancel()
            
        self._panel_task = self.bot.loop.create_task(self._replace_panel(message.channel))
        
    async def _replace_panel(self, channel: discord.TextChannel):
        import asyncio
        try:
            # 10s debounce buffer to avoid spamming panel updates during active chat
            await asyncio.sleep(10.0)
            
            old_id = await self.db.get_setting("ranking_panel_id")
            if old_id:
                recent_messages = [msg.id async for msg in channel.history(limit=5)]
                if old_id in recent_messages:
                    return
                try:
                    old_msg = await channel.fetch_message(old_id)
                    await old_msg.delete()
                except discord.NotFound:
                    pass
                    
            embed = discord.Embed(
                title="🏆 Server Leaderboard",
                description="Click the button below to view the live ranking leaderboard!",
                color=discord.Color.gold()
            )
            view = LeaderboardLauncherView()
            new_msg = await channel.send(embed=embed, view=view, silent=True)
            
            await self.db.set_setting("ranking_panel_id", new_msg.id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error replacing sticky panel: {e}")

    @app_commands.command(name="setupranking", description="Setup the live ranking leaderboard button in this channel")
    @app_commands.default_permissions(administrator=True)
    async def setupranking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        embed = discord.Embed(
            title="🏆 Server Leaderboard",
            description="Click the button below to view the live ranking leaderboard!",
            color=discord.Color.gold()
        )
        
        view = LeaderboardLauncherView()
        new_msg = await interaction.channel.send(embed=embed, view=view)
        
        await self.db.set_setting("ranking_panel_id", new_msg.id)
        await interaction.followup.send("Ranking button setup complete!", ephemeral=True)

    @app_commands.command(name="checkrank", description="Check a user's rank")
    async def check_rank(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target_user = user or interaction.user
        await interaction.response.defer(ephemeral=True)
        
        ranking_service = self.bot.container.get('RankingService')
        rank = await ranking_service.get_player_rank(target_user.id)
        
        if rank:
            await interaction.followup.send(f"{target_user.mention} is currently ranked at **{rank}**.", ephemeral=True)
        else:
            await interaction.followup.send(f"{target_user.mention} is currently **Unranked**.", ephemeral=True)

    @app_commands.command(name="botversion", description="Check the current version of the bot")
    async def check_version(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"**Ticket Bot Version:** `{Config.VERSION}`", ephemeral=True)

    @app_commands.command(name="topwinrates", description="View the top 10 winrate leaderboard")
    @app_commands.describe(min_matches="Minimum number of matches to qualify (default: 3)")
    async def topwinrates(self, interaction: discord.Interaction, min_matches: int = 3):
        import time
        import traceback
        await interaction.response.defer(ephemeral=False)
        
        try:
            # Gather evidence: Time the database query
            t0 = time.time()
            raw_data = await self.db.get_top_winrates(min_matches=min_matches, limit=10)
            t1 = time.time()
            db_time = t1 - t0
            
            entries = []
            # Gather evidence: Time the Discord API calls
            t_api_start = time.time()
            for stat in raw_data:
                user_id = stat.get('_id')
                if not user_id: continue
                
                # Try to resolve user
                member = interaction.guild.get_member(user_id)
                if member:
                    name_display = member.mention
                else:
                    try:
                        user = await self.bot.fetch_user(user_id)
                        name_display = f"`{user.name}`"
                    except discord.NotFound:
                        name_display = f"`Unknown User ({user_id})`"
                    except discord.HTTPException:
                        name_display = f"`Invalid User ID ({user_id})`"
                
                entries.append((name_display, stat))
            
            t_api_end = time.time()
            api_time = t_api_end - t_api_start
                
            embed = TicketEmbeds.winrate_leaderboard_embed(entries, min_matches)
            await interaction.followup.send(embed=embed)
            
            # Send diagnostics directly to Discord
            await interaction.followup.send(f"**[Diagnostic Data]**\n- DB Query: `{db_time:.3f}s`\n- Discord API: `{api_time:.3f}s`", ephemeral=True)
            
        except Exception as e:
            error_msg = f"**Command Crashed!**\n```py\n{type(e).__name__}: {str(e)}\n{traceback.format_exc()[-1000:]}```"
            await interaction.followup.send(error_msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Ranking(bot))
