import discord
from discord.ext import commands
from discord import app_commands
import re
from typing import List, Optional
from datetime import datetime

import asyncio
import aiohttp

from database import Database
from config import Config
from utils.embeds import TicketEmbeds

async def _get_roblox_avatar_url(roblox_id: str) -> str:
    """Fetch the Roblox avatar thumbnail. Same pattern as ranking_service.py."""
    url = f"https://thumbnails.roblox.com/v1/users/avatar?userIds={roblox_id}&size=352x352&format=Png&isCircular=false"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("data") and len(data["data"]) > 0:
                        return data["data"][0].get("imageUrl", "")
    except Exception as e:
        print(f"Error fetching Roblox avatar for {roblox_id}: {e}")
    return ""

class ObserverSelect(discord.ui.Select):
    def __init__(self, observers: list):
        options = []
        for obs in observers[:25]:
            options.append(discord.SelectOption(
                label=obs.display_name[:100],
                value=str(obs.id),
                description=f"@{obs.name}"[:100],
            ))
        super().__init__(
            placeholder="Select an observer to view stats...",
            options=options,
            custom_id="observer_stats_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        observer_id = int(self.values[0])
        member = interaction.guild.get_member(observer_id)
        if not member:
            await interaction.followup.send("Observer not found in server.", ephemeral=True)
            return

        db = interaction.client.db
        ranking_service = interaction.client.container.get('RankingService')

        # Gather stats concurrently
        current_rank, total_obs = await asyncio.gather(
            db.get_player_rank(observer_id),
            db.get_observer_total_observations(observer_id),
        )

        # Roblox avatar
        roblox_avatar_url = ""
        roblox_data = await ranking_service.get_roblox_data(observer_id)
        if roblox_data:
            _, roblox_id = roblox_data
            roblox_avatar_url = await _get_roblox_avatar_url(roblox_id)

        embed = TicketEmbeds.observer_stats_embed(
            member=member,
            roblox_avatar_url=roblox_avatar_url,
            current_rank=current_rank or "Unranked",
            total_observations=total_obs,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

class ObserverSelectView(discord.ui.View):
    def __init__(self, observers: list):
        super().__init__(timeout=120)
        self.add_item(ObserverSelect(observers))

TIERS = ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]


from utils.ranking_utils import *


class RankingPaginationView(discord.ui.View):
    def __init__(self, current_page=0):
        super().__init__(timeout=None)
        self.current_page = current_page
        
    @discord.ui.button(emoji="<:left:1538261389217501436>", style=discord.ButtonStyle.secondary, custom_id="ranking_back")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ranking_service = interaction.client.container.get('RankingService')
        if not ranking_service: return
        self.current_page = max(0, self.current_page - 1)
        embeds, files = await ranking_service.generate_leaderboard_content(self.current_page)
        attachments = files if files else []
        await interaction.edit_original_response(content=None, embeds=embeds, attachments=attachments, view=self)

    @discord.ui.button(emoji="<:right:1538261357219160124>", style=discord.ButtonStyle.secondary, custom_id="ranking_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ranking_service = interaction.client.container.get('RankingService')
        if not ranking_service: return
        self.current_page = min(len(TIERS) - 1, self.current_page + 1)
        embeds, files = await ranking_service.generate_leaderboard_content(self.current_page)
        attachments = files if files else []
        await interaction.edit_original_response(content=None, embeds=embeds, attachments=attachments, view=self)

class LeaderboardLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="Ranking", style=discord.ButtonStyle.secondary, custom_id="view_leaderboard_btn", emoji=discord.PartialEmoji(id=1537488071258538045, name="rank"))
    async def view_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        ranking_service = getattr(interaction.client, 'container', None) and interaction.client.container.get('RankingService')
        if not ranking_service: 
            await interaction.response.send_message("Bot is starting up...", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        embeds, files = await ranking_service.generate_leaderboard_content(0)
        view = RankingPaginationView(0)
        kwargs = {"embeds": embeds, "view": view, "ephemeral": True}
        if files:
            kwargs["files"] = files
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

    @discord.ui.button(label="Observers", style=discord.ButtonStyle.secondary, custom_id="view_observers_btn", emoji=discord.PartialEmoji(id=1537491259957182574, name="observers"))
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
            
        view = ObserverSelectView(observers)
        embed = discord.Embed(
            title="👀 Server Observers",
            description="Select an observer below to view their stats.",
            color=discord.Color(0x2b2d31)
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
