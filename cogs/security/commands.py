from datetime import timezone

import discord
from discord import app_commands
from discord.ext import commands

from config import Config
from utils.admin_alerts import send_master_admin_dm

REQUIRED_PERMISSIONS = (
    "view_audit_log", "manage_roles", "manage_channels", "manage_webhooks", "manage_guild",
    "ban_members", "kick_members", "moderate_members", "manage_messages",
)


def _security_guild(bot):
    return bot.get_guild(Config.GUILD_ID) if Config.GUILD_ID else (bot.guilds[0] if bot.guilds else None)


def is_master(user_id: int, guild) -> bool:
    return user_id in (Config.MASTER_ADMIN_ID, Config.SHEN_ID) or (guild is not None and user_id == guild.owner_id)


def is_security_admin(user_id: int, guild) -> bool:
    if is_master(user_id, guild):
        return True
    if guild is None or not Config.CO_OWNER_ROLE_ID:
        return False
    member = guild.get_member(user_id)
    return member is not None and any(role.id == Config.CO_OWNER_ROLE_ID for role in member.roles)


class SecurityCommands(commands.GroupCog, group_name="security", group_description="Anti-nuke and anti-raid controls"):
    whitelist = app_commands.Group(name="whitelist", description="Users and bots trusted by anti-nuke")
    backupchannel = app_commands.Group(name="backupchannel", description="Channels whose messages are backed up")

    def __init__(self, bot):
        self.bot = bot
        super().__init__()

    @property
    def antinuke(self):
        return self.bot.get_cog("AntiNuke")

    @property
    def antiraid(self):
        return self.bot.get_cog("AntiRaid")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_security_admin(interaction.user.id, interaction.guild or _security_guild(self.bot)):
            return True
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return False

    async def _notify_masters_of_change(self, interaction, text):
        if not is_master(interaction.user.id, interaction.guild):
            await send_master_admin_dm(self.bot, content=f"🛡️ {interaction.user} ({interaction.user.id}) {text}")

    # ------------------------------------------------------------------ status

    @app_commands.command(name="status", description="Show the bot's security setup")
    async def status(self, interaction: discord.Interaction):
        guild = interaction.guild
        me = guild.me
        perms = me.guild_permissions
        missing = [p for p in REQUIRED_PERMISSIONS if not perms.administrator and not getattr(perms, p)]
        top_role = max(guild.roles, key=lambda r: r.position)
        db = self.bot.db
        whitelist = await db.get_security_whitelist()
        backups = await db.get_backup_channels()
        lockdown = await db.get_lockdown(guild.id)
        snapshot = await db.get_security_snapshot()

        lines = [
            f"**Bot role is highest:** {'✅' if me.top_role == top_role else f'❌ (move it above @{top_role.name})'}",
            f"**Permissions:** {'✅' if not missing else '❌ missing ' + ', '.join(missing)}",
            f"**2FA for moderation:** {'✅' if guild.mfa_level == discord.MFALevel.require_2fa else '❌ turn it on in Server Settings > Safety'}",
            f"**Whitelist:** {len(whitelist)} user(s)",
            f"**Backup channels:** {', '.join(f'<#{c}>' for c in backups) or 'none'}",
            f"**Lockdown:** {'🔒 active' + (' (manual)' if lockdown.get('manual') else '') if lockdown else 'off'}",
            f"**Last snapshot:** {discord.utils.format_dt(snapshot['created_at'].replace(tzinfo=timezone.utc), 'R') if snapshot else 'none yet'}",
        ]
        await interaction.response.send_message(
            embed=discord.Embed(title="🛡️ Security status", description="\n".join(lines), color=discord.Color.blurple()),
            ephemeral=True,
        )

    # --------------------------------------------------------------- whitelist

    @whitelist.command(name="add", description="Trust a user or bot (anti-nuke will ignore them)")
    async def whitelist_add(self, interaction: discord.Interaction, user: discord.User):
        ids = set(await self.bot.db.get_security_whitelist())
        ids.add(user.id)
        await self.bot.db.set_security_whitelist(ids)
        await self._notify_masters_of_change(interaction, f"added {user} ({user.id}) to the security whitelist")
        await interaction.response.send_message(f"Whitelisted {user.mention}.", ephemeral=True)

    @whitelist.command(name="remove", description="Stop trusting a user or bot")
    async def whitelist_remove(self, interaction: discord.Interaction, user: discord.User):
        ids = set(await self.bot.db.get_security_whitelist())
        ids.discard(user.id)
        await self.bot.db.set_security_whitelist(ids)
        await self._notify_masters_of_change(interaction, f"removed {user} ({user.id}) from the security whitelist")
        await interaction.response.send_message(f"Removed {user.mention} from the whitelist.", ephemeral=True)

    @whitelist.command(name="list", description="List trusted users and bots")
    async def whitelist_list(self, interaction: discord.Interaction):
        ids = await self.bot.db.get_security_whitelist()
        text = "\n".join(f"<@{i}> (`{i}`)" for i in sorted(ids)) or "Nobody is whitelisted (only the server owner is trusted)."
        await interaction.response.send_message(text[:2000], ephemeral=True)

    # ---------------------------------------------------------- backup channels

    @backupchannel.command(name="add", description="Back up the last 500 messages of a channel")
    async def backupchannel_add(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ids = set(await self.bot.db.get_backup_channels())
        ids.add(channel.id)
        await self.bot.db.set_backup_channels(ids)
        count = await self.antinuke.backfill_channel(channel)
        await interaction.followup.send(f"Backing up {channel.mention}. Saved {count} existing message(s).", ephemeral=True)

    @backupchannel.command(name="remove", description="Stop backing up a channel and delete its backup")
    async def backupchannel_remove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        ids = set(await self.bot.db.get_backup_channels())
        ids.discard(channel.id)
        await self.bot.db.set_backup_channels(ids)
        await self.bot.db.delete_message_backup(channel.id)
        await interaction.response.send_message(f"Stopped backing up {channel.mention}.", ephemeral=True)

    @backupchannel.command(name="list", description="List backed-up channels")
    async def backupchannel_list(self, interaction: discord.Interaction):
        ids = await self.bot.db.get_backup_channels()
        await interaction.response.send_message("\n".join(f"<#{i}>" for i in sorted(ids)) or "No backup channels.", ephemeral=True)

    # ---------------------------------------------------------------- lockdown

    @app_commands.command(name="lockdown", description="Stop @everyone from sending messages server-wide")
    async def lockdown(self, interaction: discord.Interaction, reason: str = "Manual lockdown"):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if await self.bot.db.get_lockdown(interaction.guild.id) is not None:
            await interaction.followup.send("The server is already locked down.", ephemeral=True)
            return
        count = await self.antiraid.lock(interaction.guild, manual=True, reason=f"{reason} (by {interaction.user})")
        await interaction.followup.send(f"Locked {count} channel(s).", ephemeral=True)

    @app_commands.command(name="unlock", description="Lift a lockdown and restore channel permissions exactly")
    async def unlock(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if await self.bot.db.get_lockdown(interaction.guild.id) is None:
            await interaction.followup.send("The server is not locked down.", ephemeral=True)
            return
        count = await self.antiraid.unlock(interaction.guild)
        await interaction.followup.send(f"Lockdown lifted, restored {count} channel(s).", ephemeral=True)

    # --------------------------------------------------------------- incidents

    @app_commands.command(name="incidents", description="Show recent anti-nuke incidents")
    async def incidents(self, interaction: discord.Interaction):
        rows = await self.bot.db.list_security_incidents()
        if not rows:
            await interaction.response.send_message("No incidents recorded.", ephemeral=True)
            return
        lines = [
            f"**#{r['id']}** <@{r['actor_id']}> — {r.get('reason')} — {r.get('status')} — "
            f"{len(r.get('restored', []))} restored, {len(r.get('failed', []))} failed"
            for r in rows
        ]
        await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True)

    @app_commands.command(name="revert", description="Re-run the revert for an incident (only redoes what is still missing)")
    async def revert(self, interaction: discord.Interaction, incident_id: int):
        await interaction.response.defer(ephemeral=True, thinking=True)
        incident = await self.bot.db.get_security_incident(incident_id)
        if incident is None:
            await interaction.followup.send("Incident not found.", ephemeral=True)
            return
        from cogs.security.antinuke import RevertReport

        guild = interaction.guild
        report = RevertReport({int(k): v for k, v in (incident.get("id_map") or {}).items()})
        await self.antinuke.revert_entries(guild, incident.get("actions", []), report)
        await self.antinuke.finish_slow_steps(guild, report, incident.get("started_at") or incident["created_at"])
        await self.bot.db.update_security_incident(incident_id, {
            "id_map": {str(k): v for k, v in report.id_map.items()},
            "restored": incident.get("restored", []) + report.restored,
            "failed": report.failed,
        })
        await interaction.followup.send(
            f"Revert re-run: {len(report.restored)} restored, {len(report.failed)} failed.\n"
            + "\n".join(report.failed[:10]),
            ephemeral=True,
        )

    @app_commands.command(name="release", description="Release a quarantined user and give their roles back")
    async def release(self, interaction: discord.Interaction, incident_id: int):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await self.antinuke.release(interaction.guild, incident_id), ephemeral=True)

    # --------------------------------------------------------------- snapshots

    @app_commands.command(name="snapshot", description="Save a backup of all roles, channels and server settings now")
    async def snapshot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        snapshot_id = await self.antinuke.take_snapshot(interaction.guild)
        await interaction.followup.send(f"Saved snapshot #{snapshot_id}.", ephemeral=True)

    @app_commands.command(name="restore", description="Recreate roles/channels missing since a snapshot (dry run by default)")
    async def restore(self, interaction: discord.Interaction, snapshot_id: int | None = None, dry_run: bool = True):
        await interaction.response.defer(ephemeral=True, thinking=True)
        snapshot = await self.bot.db.get_security_snapshot(snapshot_id)
        if snapshot is None:
            await interaction.followup.send("Snapshot not found. Use `/security snapshot` to make one.", ephemeral=True)
            return
        guild = interaction.guild
        missing_roles, missing_channels = self.antinuke.snapshot_diff(guild, snapshot)
        summary = (
            f"Snapshot #{snapshot['id']} ({discord.utils.format_dt(snapshot['created_at'].replace(tzinfo=timezone.utc), 'f')}): "
            f"{len(missing_roles)} missing role(s), {len(missing_channels)} missing channel(s).\n"
            + "\n".join([f"@{r['name']}" for r in missing_roles[:15]] + [f"#{c['name']}" for c in missing_channels[:15]])
        )
        if dry_run:
            await interaction.followup.send(summary[:1900] + "\n\nRun again with `dry_run: False` to restore.", ephemeral=True)
            return
        report = await self.antinuke.restore_snapshot(guild, snapshot)
        await interaction.followup.send(
            f"Restored: {len(report.restored)}, failed: {len(report.failed)}.\n" + "\n".join((report.failed + report.notes)[:15]),
            ephemeral=True,
        )

    # ------------------------------------------------------- release DM button

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("security_release:"):
            return
        guild = _security_guild(self.bot)
        if guild is None or not is_security_admin(interaction.user.id, guild):
            await interaction.response.send_message("You do not have permission to do this.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        result = await self.antinuke.release(guild, int(custom_id.split(":", 1)[1]))
        await interaction.followup.send(result)


async def setup(bot):
    await bot.add_cog(SecurityCommands(bot))
