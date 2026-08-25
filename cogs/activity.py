import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List

from config import Config
from database import Database

class ActivityCheck(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.active_check = None
        bot.loop.create_task(self.db.init())
        self.check_reminders.start()
    
    def cog_unload(self):
        self.check_reminders.cancel()
    
    @tasks.loop(minutes=5)
    async def check_reminders(self):
        if not self.active_check:
            return
        
        now = datetime.utcnow()
        deadline = self.active_check["deadline"]
        
        if now >= deadline:
            await self.finalize_check()
            return
        
        last_reminder = self.active_check.get("last_reminder")
        if last_reminder:
            last_time = datetime.fromisoformat(last_reminder)
            if (now - last_time).total_seconds() < 12 * 3600:
                return
        
        self.active_check["last_reminder"] = str(now)
        
        for user_id in self.active_check["pending_users"]:
            user = self.bot.get_user(user_id)
            if user:
                try:
                    await user.send(
                        f"**Activity Check**\n\n"
                        f"You have not reacted to the activity check message yet!\n"
                        f"If you do not react by **{deadline.strftime('%Y-%m-%d %H:%M UTC')}**, "
                        f"you will be **unranked**.\n\n"
                        f"Please react with ✅ to the activity check message in the server."
                    )
                except:
                    pass
    
    async def finalize_check(self):
        if not self.active_check:
            return
        
        message_id = self.active_check.get("message_id")
        channel_id = self.active_check.get("channel_id")
        
        channel = self.bot.get_channel(channel_id)
        if channel and message_id:
            try:
                message = await channel.fetch_message(message_id)
                
                reacted_users = set()
                for reaction in message.reactions:
                    if str(reaction.emoji) == "✅":
                        async for user in reaction.users():
                            if not user.bot:
                                reacted_users.add(user.id)
                
                pending = self.active_check["pending_users"]
                unranked = [uid for uid in pending if uid not in reacted_users]
                safe = [uid for uid in pending if uid in reacted_users]
                
                for user_id in unranked:
                    from ladder_utils import TIERS, parse_rank
                    async with self.db.ladder_lock:
                        player = await self.db.player_ranks.find_one({"user_id": user_id})
                        if player and player.get("rank"):
                            rank = player["rank"]
                            parsed = parse_rank(rank)
                            if parsed and parsed[0] in ["Novice", "Masters", "Legends"]:
                                await self.db.remove_player_from_ladder(user_id)
                                print(f"Activity check: Unranked {user_id} (was {rank})")
                
                await channel.send(
                    f"**Activity Check Complete**\n\n"
                    f"✅ Reacted: {len(safe)} players\n"
                    f"❌ Unranked: {len(unranked)} players\n"
                )
                
            except Exception as e:
                print(f"Activity check finalize error: {e}")
        
        self.active_check = None
    
    @app_commands.command(name="startactivitycheck", description="Start a 48-hour activity check for Novice, Masters, and Legends")
    @app_commands.default_permissions(administrator=True)
    async def start_activity_check(self, interaction: discord.Interaction):
        if interaction.user.id not in [Config.MASTER_ADMIN_ID, Config.SHEN_ID]:
            await interaction.response.send_message("Only Owners and Co-Owners can start an activity check.", ephemeral=True)
            return
        
        if self.active_check:
            await interaction.response.send_message("An activity check is already running!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        all_players = await self.db.get_all_player_ranks()
        
        target_users = []
        for player in all_players:
            rank = player.get("rank", "")
            from ladder_utils import parse_rank
            parsed = parse_rank(rank)
            if parsed and parsed[0] in ["Novice", "Masters", "Legends"]:
                target_users.append(player["user_id"])
        
        if not target_users:
            await interaction.followup.send("No players in Novice, Masters, or Legends tiers found.", ephemeral=True)
            return
        
        channel = interaction.channel
        
        mentions = " ".join([f"<@{uid}>" for uid in target_users])
        
        deadline = datetime.utcnow() + timedelta(hours=48)
        
        embed = discord.Embed(
            title="Activity Check",
            description=(
                f"**All Novice, Masters, and Legends players must react to this message!**\n\n"
                f"React with ✅ to confirm you are active.\n\n"
                f"**Deadline:** {deadline.strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"If you do not react by the deadline, you will be **unranked**.\n\n"
                f"You will receive a reminder every 12 hours until you react."
            ),
            color=discord.Color.orange()
        )
        
        message = await channel.send(content=mentions, embed=embed)
        await message.add_reaction("✅")
        
        self.active_check = {
            "message_id": message.id,
            "channel_id": channel.id,
            "deadline": deadline,
            "pending_users": target_users,
            "last_reminder": None
        }
        
        await interaction.followup.send(
            f"Activity check started! {len(target_users)} players have 48 hours to react.",
            ephemeral=True
        )
    
    @app_commands.command(name="cancelactivitycheck", description="Cancel the current activity check")
    @app_commands.default_permissions(administrator=True)
    async def cancel_activity_check(self, interaction: discord.Interaction):
        if interaction.user.id not in [Config.MASTER_ADMIN_ID, Config.SHEN_ID]:
            await interaction.response.send_message("Only Owners and Co-Owners can cancel an activity check.", ephemeral=True)
            return
        
        if not self.active_check:
            await interaction.response.send_message("No activity check is running.", ephemeral=True)
            return
        
        self.active_check = None
        await interaction.response.send_message("Activity check cancelled.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ActivityCheck(bot))