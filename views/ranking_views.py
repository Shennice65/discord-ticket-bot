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
        cog = interaction.client.get_cog('Ranking')
        if not cog: return
        self.current_page = max(0, self.current_page - 1)
        embeds, file = await cog.generate_leaderboard_content(self.current_page)
        attachments = [file] if file else []
        await interaction.response.edit_message(content=None, embeds=embeds, attachments=attachments, view=self)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary, custom_id="ranking_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('Ranking')
        if not cog: return
        self.current_page = min(len(TIERS) - 1, self.current_page + 1)
        embeds, file = await cog.generate_leaderboard_content(self.current_page)
        attachments = [file] if file else []
        await interaction.response.edit_message(content=None, embeds=embeds, attachments=attachments, view=self)

class LeaderboardLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="View Leaderboard", style=discord.ButtonStyle.primary, custom_id="view_leaderboard_btn", emoji="🏆")
    async def view_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('Ranking')
        if not cog: 
            await interaction.response.send_message("Bot is starting up...", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        embeds, file = await cog.generate_leaderboard_content(0)
        view = RankingPaginationView(0)
        kwargs = {"embeds": embeds, "view": view, "ephemeral": True}
        if file:
            kwargs["file"] = file
        await interaction.followup.send(**kwargs)

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
            
        # Format the observers
        desc = "# 👀 Server Observers\n\n"
        for observer in observers:
            desc += f"→ {observer.display_name} {observer.mention}\n"
                
        await interaction.response.send_message(desc, ephemeral=True)
