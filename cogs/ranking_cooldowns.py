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

class RankingCooldowns(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    async def reset_request(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        success = await self.db.reset_ranked_cooldown(user.id)
        if success:
            await interaction.followup.send(f"Reset ranked request cooldown for {user.mention}! They can now request another match immediately.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🔵 Request Cooldown Reset",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Reset By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"{user.mention} does not currently have an active cooldown.", ephemeral=True)
            
    @app_commands.command(name="clearunrank", description="Clear a player's unrank penalty (1-month re-rank ban and R1 restriction)")
    async def clear_unrank(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        success = await self.db.clear_unrank_penalty(user.id)
        if success:
            await interaction.followup.send(f"Cleared unrank penalty for {user.mention}. They can now be re-ranked and request R1s freely.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🟢 Unrank Penalty Cleared",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Cleared By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"{user.mention} does not have an active unrank penalty.", ephemeral=True)

    @app_commands.command(name="resetalltimers", description="Reset ALL timers (ranked, observation, rematch, unrank penalty) for a player")
    async def reset_all_timers_cmd(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        await self.db.reset_all_timers(user.id)
        
        await interaction.followup.send(f"Reset ALL timers (Ranked, Observation, Rematch, Unrank) for {user.mention}!", ephemeral=True)
        
        log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="🔵 All Timers Reset",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
            embed.add_field(name="Reset By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
            embed.set_footer(text=f"User ID: {user.id}")
            
            try:
                await log_channel.send(embed=embed)
            except Exception:
                pass

    async def allow_rematch(self, interaction: discord.Interaction, player1: discord.User, player2: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        
        if player1.id == player2.id:
            await interaction.response.send_message("You must select two different players!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        # Check if there's even an active cooldown
        cooldown = await self.db.get_rematch_cooldown(player1.id, player2.id)
        if cooldown <= 0:
            await interaction.followup.send(f"There is no active rematch cooldown between {player1.mention} and {player2.mention}.", ephemeral=True)
            return
        
        success = await self.db.reset_rematch_cooldown(player1.id, player2.id)
        if success:
            await interaction.followup.send(f"Rematch cooldown cleared! {player1.mention} and {player2.mention} can now face each other again.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🔄 Rematch Cooldown Cleared",
                    color=discord.Color.teal(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Player 1", value=f"{player1.mention}\n`{player1.name}`", inline=True)
                embed.add_field(name="Player 2", value=f"{player2.mention}\n`{player2.name}`", inline=True)
                embed.add_field(name="Cleared By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send("Failed to clear rematch cooldown. No recent match found between these players.", ephemeral=True)

    @app_commands.command(name="giveobscd", description="Give a user a 2-week personal observation cooldown (Observer only)")
    @app_commands.describe(user="The user to put on cooldown")
    async def give_obs_cd(self, interaction: discord.Interaction, user: discord.Member):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        try:
            await self.db.update_obs_cooldown(user.id)
            await interaction.followup.send(f"Successfully applied a 2-week personal observation cooldown to {user.mention}.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🛑 Observation Cooldown Applied",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Applied By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.add_field(name="Duration", value="2 Weeks", inline=False)
                await log_channel.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)

    @app_commands.command(name="removeobscd", description="Remove the personal observation cooldown from a user (Observer only)")
    @app_commands.describe(user="The user to remove the cooldown from")
    async def remove_obs_cd(self, interaction: discord.Interaction, user: discord.Member):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        try:
            success = await self.db.reset_obs_cooldown(user.id)
            if not success:
                await interaction.followup.send(f"{user.mention} doesn't have an active observation cooldown.", ephemeral=True)
                return
                
            await interaction.followup.send(f"Successfully removed the personal observation cooldown from {user.mention}.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🟢 Observation Cooldown Removed",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Removed By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                await log_channel.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(RankingCooldowns(bot))
