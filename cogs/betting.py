from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymongo import ReturnDocument

from config import Config
from core.services.challonge_service import ChallongeError, ChallongeService


logger = logging.getLogger(__name__)


class BettingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.challonge = ChallongeService(bot.db)

    async def cog_load(self):
        self.notification_worker.start()

    async def cog_unload(self):
        self.notification_worker.cancel()

    @staticmethod
    def _notification_embed(event: dict, site_url: str) -> discord.Embed:
        payload = event.get("payload") or {}
        team1 = payload.get("team1") or "TBD"
        team2 = payload.get("team2") or "TBD"
        matchup = f"**{team1}** vs **{team2}**"
        event_type = event.get("event_type")
        styles = {
            "betting_opened": ("🪙 Betting is open", discord.Color.green(), f"{matchup}\nChoose a team before predictions lock."),
            "betting_locked": ("🔒 Betting is locked", discord.Color.orange(), f"{matchup}\nNo more wagers can be placed."),
            "match_live": ("🔴 Match is live", discord.Color.red(), f"{matchup}\nThe market is locked while the teams play."),
            "result_confirmed": ("🏆 Result confirmed", discord.Color.blue(), f"{matchup}\n**Final: {payload.get('score1', 0)}–{payload.get('score2', 0)}**"),
            "match_restored": ("↩️ Match result withdrawn", discord.Color.orange(), f"{matchup}\nThe previous result was restored by an organizer. Existing wagers are active again; wait for the corrected result."),
        }
        title, color, description = styles.get(event_type, ("Tournament update", discord.Color.blurple(), matchup))
        if event_type == "result_confirmed":
            if payload.get("winner_name"):
                description += f"\nWinner: **{payload['winner_name']}**"
            else:
                description += "\nResult: **Draw**"
            if payload.get("refunded"):
                description += "\nAll active wagers were refunded."
            elif payload.get("payout_count"):
                description += f"\nPaid **{payload['payout_count']}** winning predictor(s)."
        match_url = f"{site_url.rstrip('/')}/matches/{event.get('match_id')}"
        embed = discord.Embed(title=title, description=description, color=color, url=match_url)
        context = " · ".join(str(value) for value in (payload.get("group"), payload.get("round")) if value)
        if context:
            embed.set_author(name=context)
        closes_at = discord.utils.parse_time(payload.get("betting_closes_at")) if payload.get("betting_closes_at") else None
        scheduled_at = discord.utils.parse_time(payload.get("scheduled_at")) if payload.get("scheduled_at") else None
        if event_type == "betting_opened" and closes_at:
            embed.add_field(name="Betting locks", value=discord.utils.format_dt(closes_at, style="R"), inline=True)
        if scheduled_at:
            embed.add_field(name="Match time", value=discord.utils.format_dt(scheduled_at, style="F"), inline=True)
        embed.add_field(name="Tournament site", value=f"[Open match]({match_url})", inline=False)
        return embed

    @staticmethod
    def _iso_datetime(value) -> str | None:
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat().replace("+00:00", "Z")

    async def _lock_due_markets(self, now: datetime) -> int:
        due_matches = await self.bot.db.betting_matches.find(
            {"state": "open", "betting_closes_at": {"$ne": None, "$lte": now}}
        ).limit(100).to_list(length=100)
        locked = 0
        for match in due_matches:
            result = await self.bot.db.betting_matches.update_one(
                {"_id": match["_id"], "state": "open"},
                {"$set": {"state": "locked", "locked_at": now, "updated_at": now}},
            )
            if not result.modified_count:
                continue
            locked += 1
            stable_match_id = str(match.get("challonge_match_id") or match.get("_id"))
            await self.bot.db.betting_admin_audit.insert_one(
                {
                    "match_id": stable_match_id,
                    "action": "automatic_lock",
                    "actor": "system",
                    "before_state": "open",
                    "after_state": "locked",
                    "created_at": now,
                }
            )
            await self.bot.db.betting_notifications.insert_one(
                {
                    "_id": f"automatic-lock:{stable_match_id}:{int(now.timestamp())}",
                    "event_type": "betting_locked",
                    "match_id": stable_match_id,
                    "payload": {
                        "team1": match.get("team1_name") or "TBD",
                        "team2": match.get("team2_name") or "TBD",
                        "group": match.get("group") or "Tournament",
                        "round": match.get("round_label") or match.get("round"),
                        "scheduled_at": self._iso_datetime(match.get("scheduled_at")),
                        "betting_closes_at": self._iso_datetime(match.get("betting_closes_at")),
                    },
                    "status": "pending",
                    "attempts": 0,
                    "created_at": now,
                    "next_attempt_at": now,
                }
            )
        return locked

    @tasks.loop(seconds=10)
    async def notification_worker(self):
        now = datetime.now(timezone.utc)
        try:
            await self._lock_due_markets(now)
            config = await self.bot.db.db.config.find_one({"_id": "api_keys"}) or {}
        except Exception:
            logger.exception("Betting notification worker could not read MongoDB")
            return
        channel_id = config.get("BETTING_NOTIFICATION_CHANNEL_ID") or Config.BETTING_NOTIFICATION_CHANNEL_ID
        if not channel_id:
            return
        try:
            await self.bot.db.betting_notifications.update_many(
                {"status": "processing", "claimed_at": {"$lte": now - timedelta(minutes=5)}},
                {"$set": {"status": "pending", "next_attempt_at": now}},
            )
            event = await self.bot.db.betting_notifications.find_one_and_update(
                {"status": "pending", "next_attempt_at": {"$lte": now}},
                {"$set": {"status": "processing", "claimed_at": now}, "$inc": {"attempts": 1}},
                sort=[("created_at", 1)],
                return_document=ReturnDocument.AFTER,
            )
        except Exception:
            logger.exception("Betting notification worker could not claim an event")
            return
        if not event:
            return
        try:
            # Notifications can remain queued while an administrator reschedules
            # a match. Always render identity and timing from the current match
            # document instead of the stale snapshot stored on the event.
            stable_match_id = str(event.get("match_id") or "")
            match_id_candidates: list[object] = [stable_match_id]
            try:
                match_id_candidates.append(int(stable_match_id))
            except ValueError:
                pass
            current_match = await self.bot.db.betting_matches.find_one(
                {
                    "$or": [
                        {"_id": {"$in": match_id_candidates}},
                        {"challonge_match_id": {"$in": match_id_candidates}},
                    ]
                }
            )
            if current_match:
                payload = dict(event.get("payload") or {})
                payload.update(
                    {
                        "team1": current_match.get("team1_name") or "TBD",
                        "team2": current_match.get("team2_name") or "TBD",
                        "group": current_match.get("group") or "Tournament",
                        "round": current_match.get("round_label") or current_match.get("round"),
                        "scheduled_at": self._iso_datetime(current_match.get("scheduled_at")),
                        "betting_closes_at": self._iso_datetime(current_match.get("betting_closes_at")),
                    }
                )
                event["payload"] = payload
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
            if not hasattr(channel, "send"):
                raise RuntimeError("Configured betting notification channel is not messageable")
            site_url = str(config.get("BETTING_SITE_URL") or Config.BETTING_SITE_URL).rstrip("/")
            embed = self._notification_embed(event, site_url)
            role_id = config.get("BETTING_NOTIFICATION_ROLE_ID") or Config.BETTING_NOTIFICATION_ROLE_ID
            mention = f"<@&{int(role_id)}>" if role_id and event.get("event_type") == "betting_opened" else None
            message = await channel.send(
                content=mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=bool(mention), users=False, everyone=False),
            )
            await self.bot.db.betting_notifications.update_one(
                {"_id": event["_id"], "status": "processing"},
                {"$set": {"status": "sent", "sent_at": datetime.now(timezone.utc), "discord_message_id": message.id}},
            )
        except Exception as error:
            logger.exception("Betting notification delivery failed")
            delay = min(300, 15 * (2 ** min(int(event.get("attempts", 1)) - 1, 4)))
            await self.bot.db.betting_notifications.update_one(
                {"_id": event["_id"], "status": "processing"},
                {
                    "$set": {
                        "status": "pending",
                        "next_attempt_at": datetime.now(timezone.utc) + timedelta(seconds=delay),
                        "last_error": type(error).__name__,
                    }
                },
            )

    @notification_worker.before_loop
    async def before_notification_worker(self):
        await self.bot.wait_until_ready()

    async def _betting_site_url(self) -> str | None:
        """Read the live website URL from MongoDB, then fall back to env config."""
        config_doc = await self.bot.db.db.config.find_one({"_id": "api_keys"}) or {}
        site_url = str(config_doc.get("BETTING_SITE_URL") or Config.BETTING_SITE_URL).strip().rstrip("/")
        parsed = urlparse(site_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            return None
        return site_url

    @app_commands.command(
        name="website-login",
        description="Get a private one-time sign-in link for the tournament website",
    )
    @app_commands.checks.cooldown(3, 300, key=lambda interaction: interaction.user.id)
    async def website_login(self, interaction: discord.Interaction):
        if Config.GUILD_ID and interaction.guild_id != Config.GUILD_ID:
            await interaction.response.send_message(
                "This command is only available in the tournament server.",
                ephemeral=True,
            )
            return

        site_url = await self._betting_site_url()
        if not site_url:
            await interaction.response.send_message(
                "The tournament website URL is not configured correctly. Please contact an administrator.",
                ephemeral=True,
            )
            return

        avatar_url = interaction.user.display_avatar.url if interaction.user.display_avatar else None
        token = await self.bot.db.create_web_login_token(
            discord_user_id=interaction.user.id,
            discord_username=str(interaction.user),
            display_name=interaction.user.display_name,
            avatar_url=avatar_url,
        )
        login_url = f"{site_url}/auth/redeem?{urlencode({'token': token})}"
        view = discord.ui.View(timeout=300)
        view.add_item(discord.ui.Button(label="Open tournament website", url=login_url))
        await interaction.response.send_message(
            "Your private sign-in link expires in **5 minutes** and works once. "
            "Do not share it.",
            view=view,
            ephemeral=True,
        )

    @website_login.error
    async def website_login_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.0f} seconds before requesting another link."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        raise error

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == Config.MASTER_ADMIN_ID:
            return True
        await interaction.response.send_message("This command is restricted to the bot owner.", ephemeral=True)
        return False

    @app_commands.command(
        name="betting-notifications",
        description="Choose the Discord channel for tournament betting updates",
    )
    @app_commands.describe(
        channel="Channel that receives betting and match updates",
        mention_role="Optional role mentioned when betting opens",
    )
    async def betting_notifications(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        mention_role: discord.Role | None = None,
    ):
        if not await self._require_owner(interaction):
            return
        await self.bot.db.db.config.update_one(
            {"_id": "api_keys"},
            {
                "$set": {
                    "BETTING_NOTIFICATION_CHANNEL_ID": str(channel.id),
                    "BETTING_NOTIFICATION_ROLE_ID": str(mention_role.id) if mention_role else None,
                }
            },
            upsert=True,
        )
        role_text = mention_role.mention if mention_role else "no role mention"
        await interaction.response.send_message(
            f"✅ Betting notifications will be sent to {channel.mention} ({role_text}).",
            ephemeral=True,
        )

    @app_commands.command(
        name="challonge-status",
        description="Test Challonge authentication and inspect the configured tournament",
    )
    @app_commands.checks.cooldown(1, 60, key=lambda interaction: interaction.user.id)
    async def challonge_status(self, interaction: discord.Interaction):
        if not await self._require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            snapshot = await self.challonge.fetch_snapshot()
            status = self.challonge.snapshot_status(snapshot)
        except ChallongeError as error:
            await interaction.followup.send(f"❌ **Challonge connection failed**\n{error}", ephemeral=True)
            return

        await interaction.followup.send(
            "\n".join(
                [
                    "✅ **Challonge connection successful**",
                    f"Tournament: **{status['tournament_name']}** (`{status['tournament_id']}`)",
                    f"Two-stage group tournament: **{'Yes' if status['group_stage_enabled'] else 'No'}**",
                    f"Participants: **{status['participants']}**",
                    f"Matches: **{status['matches']}** ({status['playable_matches']} currently have both players)",
                    f"Challonge states: **{status['open_matches']} open**, **{status['completed_matches']} complete**",
                    "No local data was changed.",
                ]
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="challonge-sync",
        description="Import the configured Challonge tournament into the website match board",
    )
    @app_commands.checks.cooldown(1, 60, key=lambda interaction: interaction.user.id)
    async def challonge_sync(self, interaction: discord.Interaction):
        if not await self._require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        local_config = await self.bot.db.db.config.find_one({"_id": "tournament_local"}) or {}
        if local_config.get("mode") == "local":
            await interaction.followup.send(
                "ℹ️ **Local tournament mode is active.** Manage scores and settlement from the website admin page; Challonge sync is disabled to protect local results.",
                ephemeral=True,
            )
            return
        try:
            snapshot = await self.challonge.fetch_snapshot()
            result = await self.challonge.sync_snapshot(snapshot)
        except ChallongeError as error:
            await interaction.followup.send(f"❌ **Challonge sync failed**\n{error}", ephemeral=True)
            return
        except Exception:
            logger.exception("Unexpected Challonge synchronization failure")
            await interaction.followup.send(
                "❌ Challonge data was fetched, but the local database sync failed. Check the bot logs.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "\n".join(
                [
                    "✅ **Challonge synchronization complete**",
                    f"Tournament: **{result['tournament_name']}** (`{result['tournament_id']}`)",
                    f"Participants imported: **{result['participants']}**",
                    f"Matches imported: **{result['matches']}**",
                    f"Playable matches: **{result['playable_matches']}**",
                    f"Default weekend times assigned: **{result['scheduled_matches']}**",
                    f"Results observed: **{result['completed_matches']} complete**",
                    f"Newly settled betting markets: **{result['settled_matches']}**",
                    "New matches remain upcoming; betting is not opened automatically.",
                ]
            ),
            ephemeral=True,
        )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.0f} seconds before calling this command again."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        raise error


async def setup(bot):
    await bot.add_cog(BettingCog(bot))
