from __future__ import annotations

import logging
from urllib.parse import urlencode, urlparse

import discord
from discord import app_commands
from discord.ext import commands

from config import Config
from core.services.challonge_service import ChallongeError, ChallongeService


logger = logging.getLogger(__name__)


class BettingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.challonge = ChallongeService(bot.db)

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
                    f"Results observed: **{result['completed_matches']} complete**",
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
