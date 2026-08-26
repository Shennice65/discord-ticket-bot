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
        self.db = None
        self.active_check = None
        self.check_reminders.start()

    @commands.Cog.listener()
    async def on_ready(self):
        tickets_cog = self.bot.get_cog('Tickets')
        if tickets_cog:
            self.db = tickets_cog.db
        else:
            ranking_cog = self.bot.get_cog('Ranking')
            if ranking_cog:
                self.db = ranking_cog.db
    
    def cog_unload(self):
        self.check_reminders.cancel()
    
    @tasks.loop(minutes=5)
    async def check_reminders(self):
        if not self.active_check:
            return

        if self.db is None or self.db.player_ranks is None:
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
        
        deadline_ts = self.active_check.get("deadline_timestamp", 0)

        reacted_users = set()
        channel_id = self.active_check.get("channel_id")
        message_id = self.active_check.get("message_id")
        channel = self.bot.get_channel(channel_id)

        if channel and message_id:
            try:
                message = await channel.fetch_message(message_id)
                for reaction in message.reactions:
                    if str(reaction.emoji) == "✅":
                        async for user in reaction.users():
                            if not user.bot:
                                reacted_users.add(user.id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                print(f"Activity check reminder reaction lookup failed: {e}")
        
        for user_id in self.active_check["pending_users"]:
            if user_id in reacted_users:
                continue

            user = self.bot.get_user(user_id)
            if user:
                try:
                    await user.send(
                        f"**Activity Check**\n\n"
                        f"You have not reacted to the activity check message yet!\n"
                        f"If you do not react by <t:{deadline_ts}:F>, "
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
                unranked_ids = [uid for uid in pending if uid not in reacted_users]
                
                tier_order = ["Legends", "Masters", "Novice"]
                
                unranked_info = []
                for user_id in unranked_ids:
                    from utils.ladder_utils import parse_rank
                    async with self.db.ladder_lock:
                        player = await self.db.player_ranks.find_one({"user_id": user_id})
                        if player and player.get("rank"):
                            rank = player["rank"]
                            parsed = parse_rank(rank)
                            if parsed and parsed[0] in ["Novice", "Masters", "Legends"]:
                                unranked_info.append((user_id, rank))
                                await self.db.remove_player_from_ladder(user_id)
                                print(f"Activity check: Unranked {user_id} (was {rank})")
                
                unranked_info.sort(key=lambda x: tier_order.index(parse_rank(x[1])[0]) if parse_rank(x[1]) and parse_rank(x[1])[0] in tier_order else 99)
                
                result_lines = []
                current_tier = None
                for user_id, rank in unranked_info:
                    parsed = parse_rank(rank)
                    tier = parsed[0] if parsed else "Unknown"
                    if tier != current_tier:
                        current_tier = tier
                        result_lines.append(f"\n**{tier}**")
                    result_lines.append(f"<@{user_id}> - {rank}")
                
                result_text = "\n".join(result_lines) if result_lines else "No one was unranked."
                
                await channel.send(
                    f"**Activity Check Complete**\n\n"
                    f"**Unranked for inactivity:**\n"
                    f"{result_text}"
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
        
        if self.db is None or self.db.player_ranks is None:
            await self.db.init()

        if self.db is None or self.db.player_ranks is None:
            await interaction.followup.send("Database not ready yet, please try again.", ephemeral=True)
            return
        
        all_players = await self.db.get_all_player_ranks()
        
        target_users = []
        for player in all_players:
            rank = player.get("rank", "")
            from utils.ladder_utils import parse_rank
            parsed = parse_rank(rank)
            if parsed and parsed[0] in ["Novice", "Masters", "Legends"]:
                target_users.append(player["user_id"])
        
        if not target_users:
            await interaction.followup.send("No players in Novice, Masters, or Legends tiers found.", ephemeral=True)
            return
        
        channel = self.bot.get_channel(Config.RANKING_PANEL_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("Rank panel channel not found!", ephemeral=True)
            return
        
        rank_role_ids = []
        if hasattr(Config, 'NOVICE_ROLE_ID') and Config.NOVICE_ROLE_ID:
            rank_role_ids.append(Config.NOVICE_ROLE_ID)
        if hasattr(Config, 'MASTERS_ROLE_ID') and Config.MASTERS_ROLE_ID:
            rank_role_ids.append(Config.MASTERS_ROLE_ID)
        if hasattr(Config, 'LEGEND_ROLE_ID') and Config.LEGEND_ROLE_ID:
            rank_role_ids.append(Config.LEGEND_ROLE_ID)

        mentions = " ".join([f"<@&{rid}>" for rid in rank_role_ids])
        
        deadline = datetime.utcnow() + timedelta(hours=48)
        deadline_timestamp = int(deadline.timestamp())
        
        embed = discord.Embed(
            title="Activity Check",
            description=(
                f"**All Novice, Masters, and Legends players must react to this message!**\n\n"
                f"React with ✅ to confirm you are active.\n\n"
                f"**Deadline:** <t:{deadline_timestamp}:F>\n"
                f"If you do not react by <t:{deadline_timestamp}:F>, you will be **unranked**.\n\n"
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
            "deadline_timestamp": deadline_timestamp,
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
