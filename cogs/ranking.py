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
        # Register both views so they persist after restart
        self.bot.add_view(RankingPaginationView())
        self.bot.add_view(LeaderboardLauncherView())
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not Config.RANKING_PANEL_CHANNEL_ID or message.channel.id != Config.RANKING_PANEL_CHANNEL_ID:
            return
            
        # Ignore if the message IS the panel itself
        if message.author == self.bot.user and message.embeds and message.embeds[0].title == "🏆 Server Leaderboard":
            return
            
        # Cancel pending task if any, to debounce
        if self._panel_task and not self._panel_task.done():
            self._panel_task.cancel()
            
        self._panel_task = self.bot.loop.create_task(self._replace_panel(message.channel))
        
    async def _replace_panel(self, channel: discord.TextChannel):
        import asyncio
        try:
            # Wait 10 seconds to debounce fast chat messages
            await asyncio.sleep(10.0)
            
            # Fetch old panel ID
            old_id = await self.db.get_setting("ranking_panel_id")
            
            # Check if the panel is already near the bottom (within last 5 messages)
            if old_id:
                recent_messages = []
                async for msg in channel.history(limit=5):
                    recent_messages.append(msg.id)
                
                if old_id in recent_messages:
                    # Panel is still very visible, no need to bump and spam notifications
                    return
            
            # If it's pushed too far up, delete the old one
            if old_id:
                try:
                    old_msg = await channel.fetch_message(old_id)
                    await old_msg.delete()
                except discord.NotFound:
                    pass
                    
            # Spawn new panel silently to avoid pinging
            embed = discord.Embed(
                title="🏆 Server Leaderboard",
                description="Click the button below to view the live ranking leaderboard!",
                color=discord.Color.gold()
            )
            view = LeaderboardLauncherView()
            new_msg = await channel.send(embed=embed, view=view, silent=True)
            
            # Save new ID
            await self.db.set_setting("ranking_panel_id", new_msg.id)
        except asyncio.CancelledError:
            # Task was cancelled by another message, which is fine (debounce)
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

async def setup(bot):
    await bot.add_cog(Ranking(bot))
