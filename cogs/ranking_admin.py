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

class RankingAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @app_commands.command(name="removeplayer", description="Remove a player from the leaderboard")
    @app_commands.describe(user="The player to remove")
    async def remove_player(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        success = await self.db.remove_player_from_ladder(user.id)
        if success:
            # Strip all tier roles since player is removed
            from utils.role_manager import update_tier_role
            await update_tier_role(interaction.guild, user.id, "")
            
            await interaction.followup.send(f"Successfully removed {user.mention} from the leaderboard! The ladder has been compressed to fill their gap.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🔴 Player Removed from Ladder",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Removed By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"{user.mention} is not currently ranked on the leaderboard.", ephemeral=True)

    @app_commands.command(name="setrank", description="Manually force a player into a specific rank, shifting others to make room")
    @app_commands.describe(user="The player to rank", rank="The exact rank (e.g., Legends 3)")
    async def set_rank(self, interaction: discord.Interaction, user: discord.User, rank: str):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        success, actual_rank = await self.db.force_set_player_rank(user.id, rank, bypass_unrank=True)
        if success:
            # Auto-assign the correct tier role
            from utils.role_manager import update_tier_role
            await update_tier_role(interaction.guild, user.id, actual_rank)
            
            await interaction.followup.send(f"Successfully slotted {user.mention} in at **{actual_rank}**! The rest of the ladder has been compressed and shifted automatically to make room.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🟡 Rank Manually Set",
                    color=discord.Color.gold(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Set By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.add_field(name="New Rank", value=f"**{actual_rank}**", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
                
            ticket_service = interaction.client.container.get('TicketService')
            if ticket_service:
                await ticket_service.check_and_notify_rank_change(user.id, actual_rank)
        else:
            await interaction.followup.send(f"Failed to set rank. Please ensure the rank is formatted correctly (e.g., `Legends 3`, `Champions 12`).", ephemeral=True)
            
    @app_commands.command(name="setstreak", description="Manually set a player's win streak")
    @app_commands.describe(user="The player to modify", streak="The new streak number")
    async def set_streak(self, interaction: discord.Interaction, user: discord.User, streak: int):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        if streak < 0:
            await interaction.followup.send("Streak cannot be negative.", ephemeral=True)
            return
            
        player = await self.db.player_ranks.find_one({"user_id": user.id})
        if not player or not player.get("rank"):
            await interaction.followup.send(f"{user.mention} is not currently ranked on the leaderboard.", ephemeral=True)
            return
            
        old_streak = player.get("win_streak", 0)
        await self.db.player_ranks.update_one({"user_id": user.id}, {"$set": {"win_streak": streak}})
        
        await interaction.followup.send(f"Successfully set {user.mention}'s win streak to **{streak}**!", ephemeral=True)
        
        log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="🔥 Streak Manually Set",
                color=discord.Color.orange(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Player", value=user.mention, inline=True)
            embed.add_field(name="Change", value=f"{old_streak} ➔ {streak}", inline=True)
            embed.add_field(name="Observer", value=interaction.user.mention, inline=False)
            try:
                await log_channel.send(embed=embed)
            except Exception as e:
                print(f"Failed to send streak log: {e}")

    @app_commands.command(name="removebyrank", description="Remove a player by specifying their rank (e.g. Legends 3)")
    @app_commands.describe(rank="The exact rank to clear (e.g. Legends 3)")
    async def remove_by_rank(self, interaction: discord.Interaction, rank: str):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
            
        from ladder_utils import parse_rank
        parsed = parse_rank(rank)
        if not parsed:
            await interaction.response.send_message(f"Invalid rank format. Please use a format like `Legends 3`.", ephemeral=True)
            return
            
        formatted_rank = f"{parsed[0]} {parsed[1]}"
        
        await interaction.response.defer(ephemeral=True)
        
        player = await self.db.get_player_by_rank(formatted_rank)
        
        if not player:
            await interaction.followup.send(f"No player is currently ranked at **{formatted_rank}**.", ephemeral=True)
            return
            
        user_id = player['user_id']
        
        # Try to get the user object for display purposes
        user = interaction.guild.get_member(user_id)
        
        success = await self.db.remove_player_from_ladder(user_id)
        
        if success:
            # Strip all tier roles since player is removed
            from utils.role_manager import update_tier_role
            await update_tier_role(interaction.guild, user_id, "")
            
            user_mention = user.mention if user else f"<@{user_id}>"
            user_name = user.name if user else f"User {user_id}"
            
            await interaction.followup.send(f"Successfully removed {user_mention} from **{formatted_rank}**! The ladder has been compressed.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                from datetime import datetime
                embed = discord.Embed(
                    title="🔴 Player Removed from Ladder (By Rank)",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user_mention}\n`{user_name}`", inline=True)
                embed.add_field(name="Removed From Rank", value=f"**{formatted_rank}**", inline=True)
                embed.add_field(name="Removed By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user_id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"Failed to remove player from {formatted_rank}.", ephemeral=True)

    @app_commands.command(name="undo", description="Undo the last rank change for a specific user")
    @app_commands.describe(user="The user whose last rank change you want to undo")
    async def undo(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        success, message = await self.db.undo_last_action(user.id)
        
        if success:
            # Update tier role to match restored rank
            from utils.role_manager import update_tier_role
            new_rank = await self.db.get_player_rank(user.id)
            await update_tier_role(interaction.guild, user.id, new_rank)
            
            await interaction.followup.send(f"Undo successful! {user.mention} has been {message}. The leaderboard shifted back.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                from datetime import datetime
                embed = discord.Embed(
                    title="↩️ Rank Action Undone",
                    color=discord.Color.purple(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Action Result", value=f"They were {message}", inline=False)
                embed.add_field(name="Undone By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
                
            ticket_service = interaction.client.container.get('TicketService')
            if ticket_service:
                await ticket_service.check_and_notify_rank_change(user.id, new_rank if new_rank else "Unranked")
        else:
            await interaction.followup.send(f"Undo failed: {message}", ephemeral=True)

    @app_commands.command(name="syncroles", description="Sync all Discord tier roles to match current ladder ranks")
    async def sync_roles(self, interaction: discord.Interaction):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        from utils.role_manager import update_tier_role
        all_players = await self.db.get_all_player_ranks()
        
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        for player in all_players:
            user_id = player.get("user_id")
            rank = player.get("rank", "")
            
            if not rank or not user_id:
                skip_count += 1
                continue
            
            try:
                await update_tier_role(interaction.guild, user_id, rank)
                success_count += 1
            except Exception:
                fail_count += 1
        
        await interaction.followup.send(
            f"**Role sync complete!**\n"
            f"• Updated: **{success_count}** players\n"
            f"• Skipped (unranked): **{skip_count}**\n"
            f"• Failed: **{fail_count}**",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(RankingAdmin(bot))
