from __future__ import annotations

import logging
import ssl
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

import discord
import certifi
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont
from pymongo import ReturnDocument

from config import Config
from core.services.challonge_service import ChallongeError, ChallongeService


logger = logging.getLogger(__name__)


class BettingCog(commands.Cog):
    BANNER_ASSET_CACHE: dict[str, bytes] = {}
    TEAM_LOGO_PATHS = {
        "cataclysm": "cataclysm.png",
        "cherrybomb": "cherrybomb.png",
        "cherryblossom": "cherrybomb.png",
        "cherryblosom": "cherrybomb.png",
        "merleura": "merleura.png",
        "nexus": "nexus.png",
        "senpai": "senpai.png",
        "toaster": "toaster.png",
        "toaster22436": "toaster.png",
        "xblaze": "x-blaze.png",
    }

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
        match_url = f"{site_url.rstrip('/')}/matches/{event.get('match_id')}"
        if event_type == "betting_opened":
            phrases = (
                "Who takes this matchup?",
                "The teams are set. Who are you backing?",
                "Pick your winner before the match begins.",
            )
            phrase = phrases[sum(ord(character) for character in str(event.get("match_id") or "")) % len(phrases)]
            return discord.Embed(
                description=f"{phrase}\n[Choose your pick →]({match_url})",
                color=discord.Color.blurple(),
                url=match_url,
            )
        styles = {
            "betting_opened": ("⚔️ A new matchup is ready", discord.Color.blurple(), f"**{team1}** steps up against **{team2}**.\nWho do you think takes it? Make your pick and follow the live predictions."),
            "betting_locked": ("🔒 Predictions are in", discord.Color.orange(), f"{matchup}\nThe picks are sealed. Now it is time to see which team delivers."),
            "match_live": ("🔴 The match is live", discord.Color.red(), f"{matchup}\nThey are playing now—good luck to everyone who made a pick!"),
            "result_confirmed": ("🏆 The result is in", discord.Color.blue(), f"{matchup}\n**Final: {payload.get('score1', 0)}–{payload.get('score2', 0)}**"),
            "match_restored": ("↩️ Result under review", discord.Color.orange(), f"{matchup}\nAn organizer withdrew the previous result. Your original prediction is still saved while we wait for the correction."),
        }
        title, color, description = styles.get(event_type, ("Tournament update", discord.Color.blurple(), matchup))
        if event_type == "result_confirmed":
            if payload.get("winner_name"):
                description += f"\nWinner: **{payload['winner_name']}**"
            else:
                description += "\nResult: **Draw**"
            if payload.get("refunded"):
                description += "\nAll prediction coins were returned."
            elif payload.get("payout_count"):
                description += f"\nPaid **{payload['payout_count']}** winning predictor(s)."
        embed = discord.Embed(title=title, description=description, color=color, url=match_url)
        context = " · ".join(str(value) for value in (payload.get("group"), payload.get("round")) if value)
        if context:
            embed.set_author(name=context)
        closes_at = discord.utils.parse_time(payload.get("betting_closes_at")) if payload.get("betting_closes_at") else None
        scheduled_at = discord.utils.parse_time(payload.get("scheduled_at")) if payload.get("scheduled_at") else None
        if event_type == "betting_opened" and closes_at:
            embed.add_field(name="Make your pick by", value=discord.utils.format_dt(closes_at, style="R"), inline=True)
        if scheduled_at:
            embed.add_field(name="Match time", value=discord.utils.format_dt(scheduled_at, style="F"), inline=True)
        embed.add_field(name="Ready to choose?", value=f"[View the matchup and make your pick →]({match_url})", inline=False)
        return embed

    @classmethod
    def _team_logo_filename(cls, team_name: str) -> str | None:
        key = "".join(character for character in team_name.lower() if character.isalnum())
        filename = cls.TEAM_LOGO_PATHS.get(key)
        if filename is None and key.startswith("toaster"):
            filename = cls.TEAM_LOGO_PATHS["toaster"]
        return filename

    @classmethod
    def _team_logo_path(cls, team_name: str) -> Path | None:
        filename = cls._team_logo_filename(team_name)
        if not filename:
            return None
        path = Path(__file__).resolve().parents[1] / "clips" / "frontend" / "public" / "teams" / filename
        return path if path.is_file() else None

    @classmethod
    def _banner_image_source(cls, local_path: Path | None, public_url: str):
        if local_path and local_path.is_file():
            return Image.open(local_path)
        content = cls.BANNER_ASSET_CACHE.get(public_url)
        if content is None:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            with urlopen(public_url, timeout=5, context=ssl_context) as response:
                content = response.read()
            cls.BANNER_ASSET_CACHE[public_url] = content
        return Image.open(BytesIO(content))

    @staticmethod
    def _banner_font(size: int):
        for font_path in (
            "DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ):
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    @classmethod
    def _matchup_banner(cls, event: dict, site_url: str) -> discord.File | None:
        if event.get("event_type") != "betting_opened":
            return None
        payload = event.get("payload") or {}
        teams = (payload.get("team1") or "TBD", payload.get("team2") or "TBD")
        try:
            canvas = Image.new("RGB", (900, 340), "#1e1e1e")
            draw = ImageDraw.Draw(canvas)

            atl_logo_path = Path(__file__).resolve().parents[1] / "clips" / "frontend" / "public" / "atl_logo.png"
            try:
                with cls._banner_image_source(
                    atl_logo_path if atl_logo_path.is_file() else None,
                    f"{site_url.rstrip('/')}/atl_logo.png",
                ) as source:
                    atl_logo = source.convert("RGBA")
                    alpha_box = atl_logo.getchannel("A").getbbox()
                    if alpha_box:
                        atl_logo = atl_logo.crop(alpha_box)
                    atl_logo.thumbnail((78, 78), Image.Resampling.LANCZOS)
                    canvas.paste(atl_logo, (24 + (78 - atl_logo.width) // 2, 18 + (78 - atl_logo.height) // 2), atl_logo)
            except Exception:
                logger.warning("Could not load ATL logo for matchup banner", exc_info=True)

            context = ": ".join(str(value) for value in (payload.get("group"), payload.get("round")) if value)
            draw.text((114, 25), "ATL Season 8", fill="#62b5f5", font=cls._banner_font(18), anchor="la")
            draw.text((114, 51), context or "Tournament matchup", fill="#ffffff", font=cls._banner_font(15), anchor="la")

            scheduled_at = discord.utils.parse_time(payload.get("scheduled_at")) if payload.get("scheduled_at") else None
            if scheduled_at:
                vietnam_time = scheduled_at.astimezone(timezone(timedelta(hours=7)))
                date_label = vietnam_time.strftime("%A, %B %d").replace(" 0", " ")
                time_label = vietnam_time.strftime("%I:%M %p").lstrip("0") + " GMT+7"
            else:
                date_label, time_label = "Schedule pending", "Time TBA"
            draw.text((870, 24), date_label, fill="#ffffff", font=cls._banner_font(15), anchor="ra")
            draw.text((870, 47), time_label, fill="#ffffff", font=cls._banner_font(15), anchor="ra")

            team_layouts = (
                (teams[0], 38, 382, 278, "#62b5f5"),
                (teams[1], 518, 862, 622, "#ff6b91"),
            )
            for team_name, left, right, logo_x, accent in team_layouts:
                draw.rounded_rectangle((left, 137, right, 292), radius=4, fill="#292929", outline="#777b80", width=2)
                logo_path = cls._team_logo_path(team_name)
                logo_filename = cls._team_logo_filename(team_name)
                if logo_filename:
                    try:
                        with cls._banner_image_source(
                            logo_path,
                            f"{site_url.rstrip('/')}/teams/{logo_filename}",
                        ) as source:
                            logo = source.convert("RGBA")
                            alpha_box = logo.getchannel("A").getbbox()
                            if alpha_box:
                                logo = logo.crop(alpha_box)
                            logo.thumbnail((105, 105), Image.Resampling.LANCZOS)
                            canvas.paste(logo, (logo_x - logo.width // 2, 161 + (105 - logo.height) // 2), logo)
                    except Exception:
                        logger.warning("Could not load %s logo for matchup banner", team_name, exc_info=True)
                        logo_filename = None
                if not logo_filename:
                    draw.ellipse((logo_x - 50, 164, logo_x + 50, 264), fill=accent)
                    draw.text((logo_x, 214), team_name[:2].upper(), fill="#ffffff", font=cls._banner_font(32), anchor="mm")

                if left < 450:
                    text_x = logo_x - 76
                    text_anchor = "ra"
                else:
                    text_x = logo_x + 76
                    text_anchor = "la"
                font_size = 24
                font = cls._banner_font(font_size)
                while font_size > 15 and draw.textbbox((0, 0), team_name, font=font)[2] > 145:
                    font_size -= 2
                    font = cls._banner_font(font_size)
                draw.text((text_x, 214), team_name, fill="#ffffff", font=font, anchor=text_anchor)

            draw.text((450, 191), "OPEN", fill="#a9d9ff", font=cls._banner_font(12), anchor="mm")
            draw.text((450, 224), "VS.", fill="#ffffff", font=cls._banner_font(28), anchor="mm")

            output = BytesIO()
            canvas.save(output, format="PNG", optimize=True)
            output.seek(0)
            return discord.File(output, filename="matchup.png")
        except Exception:
            logger.exception("Could not build matchup notification banner")
            return None

    @classmethod
    def _notification_embeds(cls, event: dict, site_url: str) -> list[discord.Embed]:
        primary = cls._notification_embed(event, site_url)
        return [primary]

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
            embeds = self._notification_embeds(event, site_url)
            matchup_banner = self._matchup_banner(event, site_url)
            if matchup_banner:
                embeds[0].set_image(url="attachment://matchup.png")
            role_id = config.get("BETTING_NOTIFICATION_ROLE_ID") or Config.BETTING_NOTIFICATION_ROLE_ID
            mention = f"<@&{int(role_id)}>" if role_id and event.get("event_type") == "betting_opened" else None
            send_options = dict(
                content=mention,
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions(roles=bool(mention), users=False, everyone=False),
            )
            if matchup_banner:
                send_options["file"] = matchup_banner
            message = await channel.send(**send_options)
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
        if not Config.GUILD_ID:
            await interaction.response.send_message(
                "Website login is unavailable until the tournament server is configured.",
                ephemeral=True,
            )
            return
        if interaction.guild_id != Config.GUILD_ID:
            await interaction.response.send_message(
                "This command is only available in the tournament server.",
                ephemeral=True,
            )
            return

        guild = self.bot.get_guild(Config.GUILD_ID)
        if guild is None:
            await interaction.response.send_message(
                "Website login must be requested from the tournament server.",
                ephemeral=True,
            )
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) and interaction.guild_id == guild.id else guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except discord.NotFound:
                await interaction.response.send_message(
                    "You must be a member of the tournament server to use the website.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException:
                await interaction.response.send_message(
                    "I could not verify your server membership right now. Please try again shortly.",
                    ephemeral=True,
                )
                return

        now = discord.utils.utcnow()
        account_eligible_at = member.created_at + timedelta(days=Config.WEB_LOGIN_MIN_ACCOUNT_AGE_DAYS)
        if account_eligible_at > now:
            await interaction.response.send_message(
                f"Your Discord account must be at least **{Config.WEB_LOGIN_MIN_ACCOUNT_AGE_DAYS} days old**. "
                f"You can request access {discord.utils.format_dt(account_eligible_at, style='R')}.",
                ephemeral=True,
            )
            return

        if member.joined_at is None:
            await interaction.response.send_message(
                "I could not verify when you joined the tournament server. Please contact an administrator.",
                ephemeral=True,
            )
            return
        membership_eligible_at = member.joined_at + timedelta(days=Config.WEB_LOGIN_MIN_MEMBERSHIP_DAYS)
        if membership_eligible_at > now:
            await interaction.response.send_message(
                f"You must be in the tournament server for at least **{Config.WEB_LOGIN_MIN_MEMBERSHIP_DAYS} days**. "
                f"You can request access {discord.utils.format_dt(membership_eligible_at, style='R')}.",
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
        if interaction.user.id in [Config.MASTER_ADMIN_ID, Config.SHEN_ID]:
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
