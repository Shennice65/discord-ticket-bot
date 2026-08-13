import discord
from discord.ext import commands
from discord import app_commands
import re
from typing import List, Optional
from datetime import datetime

from database import Database
from config import Config

TIERS = ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]


from utils.ranking_utils import *


class RankingPaginationView(discord.ui.View):
    def __init__(self, current_page=0):
        super().__init__(timeout=None)
        self.current_page = current_page
        
    @discord.ui.button(label="◀️ Back", style=discord.ButtonStyle.secondary, custom_id="ranking_back")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ranking_service = interaction.client.container.get('RankingService')
        if not ranking_service: return
        self.current_page = max(0, self.current_page - 1)
        embeds, file = await ranking_service.generate_leaderboard_content(self.current_page)
        attachments = [file] if file else []
        await interaction.response.edit_message(content=None, embeds=embeds, attachments=attachments, view=self)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary, custom_id="ranking_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ranking_service = interaction.client.container.get('RankingService')
        if not ranking_service: return
        self.current_page = min(len(TIERS) - 1, self.current_page + 1)
        embeds, file = await ranking_service.generate_leaderboard_content(self.current_page)
        attachments = [file] if file else []
        await interaction.response.edit_message(content=None, embeds=embeds, attachments=attachments, view=self)

class LeaderboardLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="Rank", style=discord.ButtonStyle.secondary, custom_id="view_leaderboard_btn", emoji=discord.PartialEmoji(id=1537488071258538045, name="rank"))
    async def view_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        ranking_service = getattr(interaction.client, 'container', None) and interaction.client.container.get('RankingService')
        if not ranking_service: 
            await interaction.response.send_message("Bot is starting up...", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        embeds, file = await ranking_service.generate_leaderboard_content(0)
        view = RankingPaginationView(0)
        kwargs = {"embeds": embeds, "view": view, "ephemeral": True}
        if file:
            kwargs["file"] = file
        await interaction.followup.send(**kwargs)

    @discord.ui.button(label="Winrate", style=discord.ButtonStyle.secondary, custom_id="view_winrate_leaderboard_btn", emoji=discord.PartialEmoji(id=1537488434103455784, name="winrate"))
    async def view_winrate_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            db = interaction.client.db
            raw_data = await db.get_top_winrates(min_matches=5, limit=10)
            
            entries = []
            for stat in raw_data:
                user_id = stat.get('_id')
                if not user_id: continue
                
                member = interaction.guild.get_member(user_id)
                if member:
                    name_display = member.mention
                else:
                    try:
                        user = await interaction.client.fetch_user(user_id)
                        name_display = f"`{user.name}`"
                    except discord.NotFound:
                        name_display = f"`Unknown User ({user_id})`"
                    except discord.HTTPException:
                        name_display = f"`Invalid User ID ({user_id})`"
                
                entries.append((name_display, stat))
                
            if not entries:
                msg = f"**Top Winrate Leaderboard**\n*No players found with at least 5 matches.*"
            else:
                msg = f"**📈 Top Winrate Leaderboard (Minimum 5 matches)**\n\n"
                for i, (name_display, stat) in enumerate(entries, 1):
                    win_rate = stat.get('win_rate', 0)
                    wins = stat.get('wins', 0)
                    losses = stat.get('losses', 0)
                    matches = stat.get('matches', 0)
                    msg += f"{i}. {name_display} — **{win_rate:.1f}%** ({wins}W / {losses}L / {matches}M)\n"
                    
            await interaction.followup.send(msg, ephemeral=True)
            
        except Exception as e:
            import traceback
            error_msg = f"**Command Crashed!**\n```py\n{type(e).__name__}: {str(e)}\n{traceback.format_exc()[-1000:]}```"
            await interaction.followup.send(error_msg, ephemeral=True)

    @discord.ui.button(label="View Observers", style=discord.ButtonStyle.secondary, custom_id="view_observers_btn", emoji="👀")
    async def view_observers(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
            
        role = interaction.guild.get_role(Config.OBSERVER_ROLE_ID)
        if not role:
            await interaction.response.send_message("Observer role not found or not configured.", ephemeral=True)
            return
            
        observers = [member for member in interaction.guild.members if role in member.roles]
        
        if not observers:
            await interaction.response.send_message("No observers found.", ephemeral=True)
            return
            
        bullet_list = "\n".join([f"• {observer.mention}" for observer in observers])
        embed = discord.Embed(
            title="👀 Server Observers",
            description=bullet_list,
            color=discord.Color(0x2b2d31)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
