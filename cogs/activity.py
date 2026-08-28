import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional, List

from config import Config
from database import Database
from utils.ladder_utils import TIERS, parse_rank

ACTIVITY_TIERS = tuple(TIERS)


class ActivityRecoveryView(discord.ui.View):
    def __init__(self, cog, requester_id: int, candidate_ids: list[int], target_tiers: tuple[str, ...], source_message_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.requester_id = requester_id
        self.candidate_ids = candidate_ids
        self.target_tiers = target_tiers
        self.source_message_id = source_message_id
        self.completed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the administrator who created this preview can confirm it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm unranking", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.completed:
            await interaction.response.send_message("This recovery has already been processed.", ephemeral=True)
            return
        self.completed = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Processing the recovered activity check…", view=self)
        result = await self.cog.apply_recovered_check(
            interaction.guild,
            self.candidate_ids,
            self.target_tiers,
            self.source_message_id,
            interaction.user.id,
        )
        await interaction.edit_original_response(content=result, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.completed = True
        await interaction.response.edit_message(content="Activity-check recovery cancelled. No ranks were changed.", view=None)
        self.stop()

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
        await self.restore_active_check()

    async def persist_active_check(self):
        if self.db is None or self.db.db is None or not self.active_check:
            return
        payload = dict(self.active_check)
        payload["deadline"] = payload["deadline"].isoformat()
        await self.db.db.activity_checks.update_one(
            {"_id": "active"},
            {"$set": payload},
            upsert=True,
        )

    async def restore_active_check(self):
        if self.db is None or self.db.db is None or self.active_check:
            return
        saved = await self.db.db.activity_checks.find_one({"_id": "active"})
        if not saved:
            return
        try:
            saved.pop("_id", None)
            saved["deadline"] = datetime.fromisoformat(saved["deadline"])
            self.active_check = saved
        except (KeyError, TypeError, ValueError) as error:
            print(f"Unable to restore activity check: {error}")

    async def clear_persisted_check(self):
        if self.db is not None and self.db.db is not None:
            await self.db.db.activity_checks.delete_one({"_id": "active"})
    
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
        await self.persist_active_check()
        
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
        if not channel or not message_id:
            print("Activity check finalize delayed: channel or message is unavailable.")
            return
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
                
                tier_order = list(ACTIVITY_TIERS)
                
                unranked_info = []
                for user_id in unranked_ids:
                    player = await self.db.player_ranks.find_one({"user_id": user_id})
                    rank = player.get("rank", "") if player else ""
                    parsed = parse_rank(rank)
                    if parsed and parsed[0] in ACTIVITY_TIERS:
                        success, previous_rank = await self.db.unrank_player(user_id, movement_source="activity_check")
                        if success:
                            unranked_info.append((user_id, previous_rank))
                            print(f"Activity check: Unranked {user_id} (was {previous_rank})")

                            guild = channel.guild
                            member = guild.get_member(user_id)
                            if member is None:
                                try:
                                    member = await guild.fetch_member(user_id)
                                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                                    member = None
                            if member:
                                rank_roles = [guild.get_role(role_id) for role_id in self.rank_role_ids()]
                                rank_roles = [role for role in rank_roles if role in member.roles]
                                if rank_roles:
                                    try:
                                        await member.remove_roles(*rank_roles, reason="Missed activity check")
                                    except (discord.Forbidden, discord.HTTPException) as role_error:
                                        print(f"Unable to remove rank roles from {user_id}: {role_error}")
                
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
                return
        
        self.active_check = None
        await self.clear_persisted_check()

    @staticmethod
    def rank_role_ids():
        names = ("PHANTOM_ROLE_ID", "CHAMPION_ROLE_ID", "ELITE_ROLE_ID", "LEGEND_ROLE_ID", "MASTERS_ROLE_ID", "NOVICE_ROLE_ID")
        return [getattr(Config, name, 0) for name in names if getattr(Config, name, 0)]

    async def remove_rank_roles(self, guild: discord.Guild, user_id: int):
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        rank_roles = [guild.get_role(role_id) for role_id in self.rank_role_ids()]
        rank_roles = [role for role in rank_roles if role in member.roles]
        if rank_roles:
            try:
                await member.remove_roles(*rank_roles, reason="Missed activity check")
            except (discord.Forbidden, discord.HTTPException) as error:
                print(f"Unable to remove rank roles from {user_id}: {error}")

    async def apply_recovered_check(self, guild, candidate_ids, target_tiers, source_message_id, administrator_id):
        unranked = []
        skipped = []
        for user_id in candidate_ids:
            player = await self.db.player_ranks.find_one({"user_id": user_id})
            rank = player.get("rank", "") if player else ""
            parsed = parse_rank(rank)
            if not parsed or parsed[0] not in target_tiers:
                skipped.append(user_id)
                continue
            success, previous_rank = await self.db.unrank_player(user_id, movement_source="activity_check_recovery")
            if success:
                unranked.append((user_id, previous_rank))
                await self.remove_rank_roles(guild, user_id)
            else:
                skipped.append(user_id)

        await self.db.db.activity_check_recoveries.insert_one({
            "source_message_id": str(source_message_id),
            "administrator_id": administrator_id,
            "candidate_ids": candidate_ids,
            "unranked_ids": [user_id for user_id, _ in unranked],
            "skipped_ids": skipped,
            "created_at": datetime.utcnow(),
        })
        preview = ", ".join(f"<@{user_id}> ({rank})" for user_id, rank in unranked[:20])
        suffix = f"\n…and {len(unranked) - 20} more." if len(unranked) > 20 else ""
        return f"Recovery complete. **{len(unranked)}** players were unranked; **{len(skipped)}** were skipped because their rank changed after the preview.\n{preview or 'No players were unranked.'}{suffix}"

    @app_commands.command(name="recoveractivitycheck", description="Preview and recover an old activity check from its message link")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(message_link="Full Discord link to the old activity-check message")
    async def recover_activity_check(self, interaction: discord.Interaction, message_link: str):
        if interaction.user.id not in [Config.MASTER_ADMIN_ID, Config.SHEN_ID]:
            await interaction.response.send_message("Only Owners and Co-Owners can recover an activity check.", ephemeral=True)
            return
        match = re.fullmatch(r"https?://(?:\w+\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)/?", message_link.strip())
        if not match:
            await interaction.response.send_message("Paste the full Discord message link, not only the message ID.", ephemeral=True)
            return
        guild_id, channel_id, message_id = map(int, match.groups())
        if not interaction.guild or interaction.guild.id != guild_id:
            await interaction.response.send_message("Run this command inside the server that contains the activity-check message.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        if self.db is None or self.db.player_ranks is None:
            await interaction.followup.send("Database is not ready.", ephemeral=True)
            return
        try:
            channel = interaction.guild.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
            await interaction.followup.send(f"The bot could not read that message: {error}", ephemeral=True)
            return

        embed = message.embeds[0] if message.embeds else None
        if not embed or (embed.title or "").strip().lower() != "activity check":
            await interaction.followup.send("That message is not recognized as an ATL activity check.", ephemeral=True)
            return

        reacted_users = set()
        for reaction in message.reactions:
            if str(reaction.emoji) == "✅":
                async for user in reaction.users(limit=None):
                    if not user.bot:
                        reacted_users.add(user.id)

        description = embed.description or ""
        named_tiers = tuple(tier for tier in ACTIVITY_TIERS if re.search(rf"\b{re.escape(tier)}\b", description, re.IGNORECASE))
        target_tiers = named_tiers or ACTIVITY_TIERS
        all_players = await self.db.get_all_player_ranks()
        eligible = []
        for player in all_players:
            parsed = parse_rank(player.get("rank", ""))
            if parsed and parsed[0] in target_tiers:
                eligible.append((player["user_id"], player["rank"]))
        candidates = [(user_id, rank) for user_id, rank in eligible if user_id not in reacted_users]

        candidate_lines = [f"• <@{user_id}> — {rank}" for user_id, rank in candidates[:25]]
        remaining = f"\n…and {len(candidates) - 25} more." if len(candidates) > 25 else ""
        tier_text = ", ".join(target_tiers)
        content = (
            f"**Recovered activity-check preview**\n"
            f"Message: `{message_id}` · Reacted: **{len(reacted_users)}**\n"
            f"Reconstructed eligible tiers: **{tier_text}**\n"
            f"Current eligible players: **{len(eligible)}** · Proposed unrank: **{len(candidates)}**\n\n"
            f"⚠️ The original participant snapshot was not saved. This preview compares the old reactions against players currently in the tiers named by the old message. Review before confirming.\n\n"
            + ("\n".join(candidate_lines) if candidate_lines else "No players would be unranked.")
            + remaining
        )
        view = ActivityRecoveryView(self, interaction.user.id, [user_id for user_id, _ in candidates], target_tiers, message_id)
        await interaction.followup.send(content, view=view, ephemeral=True)
    
    @app_commands.command(name="startactivitycheck", description="Start a 48-hour activity check for every ranked player")
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
            parsed = parse_rank(rank)
            if parsed and parsed[0] in ACTIVITY_TIERS:
                target_users.append(player["user_id"])
        
        if not target_users:
            await interaction.followup.send("No ranked players were found.", ephemeral=True)
            return
        
        channel = self.bot.get_channel(Config.RANKING_PANEL_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("Rank panel channel not found!", ephemeral=True)
            return
        
        rank_role_ids = self.rank_role_ids()

        mentions = " ".join([f"<@&{rid}>" for rid in rank_role_ids])
        
        deadline = datetime.utcnow() + timedelta(hours=48)
        deadline_timestamp = int(deadline.timestamp())
        
        embed = discord.Embed(
            title="Activity Check",
            description=(
                f"**All ranked players must react to this message!**\n\n"
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
        await self.persist_active_check()
        
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
        await self.clear_persisted_check()
        await interaction.response.send_message("Activity check cancelled.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ActivityCheck(bot))
