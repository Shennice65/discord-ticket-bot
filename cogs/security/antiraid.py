import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from config import Config
from utils.admin_alerts import send_security_alert
from utils.security.spam import (
    ACCOUNT_AGE_GATE,
    LOCKDOWN_QUIET_SECONDS,
    SPIKE_KICK_AGE,
    JoinSpikeDetector,
    SpamDetector,
    StrikeLadder,
    account_age,
    locked_pair,
    mentions_everyone,
)

REASON = "Anti-raid"
STAFF_ROLE_IDS = ("OBSERVER_ROLE_ID", "TRIAL_OBSERVER_ROLE_ID", "HEAD_OBSERVER_ROLE_ID", "CO_OWNER_ROLE_ID")


class AntiRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.spikes = JoinSpikeDetector()
        self.spam = SpamDetector()
        self.strikes = StrikeLadder()
        self.last_spike_at = 0.0

    async def cog_load(self):
        self.unlock_loop.start()

    async def cog_unload(self):
        self.unlock_loop.cancel()

    @staticmethod
    def is_protected_guild(guild) -> bool:
        return guild is not None and (not Config.GUILD_ID or guild.id == Config.GUILD_ID)

    async def is_staff(self, member) -> bool:
        if member.id in await self.bot.db.get_security_whitelist() or member.id == member.guild.owner_id:
            return True
        perms = member.guild_permissions
        if perms.administrator or perms.manage_messages:
            return True
        staff_roles = {getattr(Config, name, 0) for name in STAFF_ROLE_IDS} - {0}
        return any(role.id in staff_roles for role in member.roles)

    # ------------------------------------------------------------------- joins

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        if not self.is_protected_guild(guild) or member.bot:
            return
        if member.id in await self.bot.db.get_security_whitelist():
            return
        try:
            age = account_age(member.created_at)
            if age < ACCOUNT_AGE_GATE:
                await self._kick(member, f"account younger than {ACCOUNT_AGE_GATE.days} days")
                return

            spike = self.spikes.record(member.id)
            lockdown = await self.bot.db.get_lockdown(guild.id)
            if spike:
                self.last_spike_at = time.monotonic()
                if lockdown is None:
                    await self.lock(guild, manual=False, reason="Join spike detected")
                    for member_id in self.spikes.recent_ids():
                        recent = guild.get_member(member_id)
                        if recent is not None and account_age(recent.created_at) < SPIKE_KICK_AGE:
                            await self._kick(recent, "joined during a raid")
                    return
            if lockdown is not None and age < SPIKE_KICK_AGE:
                await self._kick(member, "joined during a raid lockdown")
        except Exception:
            traceback.print_exc()

    async def _kick(self, member, why):
        try:
            await member.send(
                f"You were removed from **{member.guild.name}** automatically ({why}). "
                "If this was a mistake, contact the staff."
            )
        except discord.HTTPException:
            pass
        try:
            await member.kick(reason=f"{REASON}: {why}")
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------------- lockdown

    async def lock(self, guild, *, manual: bool, reason: str) -> int:
        """Deny @everyone sending in every channel where it currently can. Returns channels locked."""
        if await self.bot.db.get_lockdown(guild.id) is not None:
            return 0
        everyone = guild.default_role
        saved = {}
        for channel in guild.text_channels:
            if not channel.permissions_for(everyone).send_messages:
                continue
            overwrite = channel.overwrites.get(everyone)
            allow, deny = overwrite.pair() if overwrite is not None else (discord.Permissions.none(), discord.Permissions.none())
            saved[str(channel.id)] = None if overwrite is None else [allow.value, deny.value]
            new_allow, new_deny = locked_pair(allow.value, deny.value)
            try:
                await channel.set_permissions(
                    everyone,
                    overwrite=discord.PermissionOverwrite.from_pair(discord.Permissions(new_allow), discord.Permissions(new_deny)),
                    reason=f"{REASON}: lockdown",
                )
            except discord.HTTPException:
                saved.pop(str(channel.id), None)

        previous_level = guild.verification_level.value
        try:
            if guild.verification_level < discord.VerificationLevel.high:
                await guild.edit(verification_level=discord.VerificationLevel.high, reason=f"{REASON}: lockdown")
        except discord.HTTPException:
            pass

        self.last_spike_at = time.monotonic()
        await self.bot.db.save_lockdown(guild.id, {
            "manual": manual,
            "reason": reason,
            "started_at": datetime.now(timezone.utc),
            "channels": saved,
            "verification_level": previous_level,
        })
        embed = discord.Embed(
            title="🔒 Server locked down",
            description=f"**Reason:** {reason}\nLocked {len(saved)} channel(s), verification raised to High.\n"
                        + ("Unlock with `/security unlock`." if manual else "Auto-unlocks after 15 quiet minutes, or `/security unlock`."),
            color=discord.Color.orange(),
        )
        await send_security_alert(self.bot, embed=embed, guild=guild)
        return len(saved)

    async def unlock(self, guild) -> int:
        lockdown = await self.bot.db.get_lockdown(guild.id)
        if lockdown is None:
            return 0
        everyone = guild.default_role
        restored = 0
        for channel_id, pair in lockdown.get("channels", {}).items():
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                continue
            overwrite = None if pair is None else discord.PermissionOverwrite.from_pair(
                discord.Permissions(pair[0]), discord.Permissions(pair[1]),
            )
            try:
                await channel.set_permissions(everyone, overwrite=overwrite, reason=f"{REASON}: lockdown lifted")
                restored += 1
            except discord.HTTPException:
                pass
        try:
            await guild.edit(
                verification_level=discord.VerificationLevel(lockdown["verification_level"]),
                reason=f"{REASON}: lockdown lifted",
            )
        except discord.HTTPException:
            pass
        await self.bot.db.clear_lockdown(guild.id)
        await send_security_alert(
            self.bot,
            embed=discord.Embed(title="🔓 Lockdown lifted", description=f"Restored {restored} channel(s).", color=discord.Color.green()),
            guild=guild,
        )
        return restored

    @tasks.loop(minutes=1)
    async def unlock_loop(self):
        for guild in self.bot.guilds:
            if not self.is_protected_guild(guild):
                continue
            lockdown = await self.bot.db.get_lockdown(guild.id)
            if lockdown is None or lockdown.get("manual"):
                continue
            if self.last_spike_at == 0.0:
                # Restarted mid-lockdown: start the quiet timer from now.
                self.last_spike_at = time.monotonic()
            if time.monotonic() - self.last_spike_at >= LOCKDOWN_QUIET_SECONDS:
                try:
                    await self.unlock(guild)
                except Exception:
                    traceback.print_exc()

    @unlock_loop.before_loop
    async def before_unlock_loop(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------------- spam

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None or not self.is_protected_guild(message.guild):
            return
        if not isinstance(message.author, discord.Member) or await self.is_staff(message.author):
            return
        try:
            if mentions_everyone(message.content):
                await message.delete()
                return

            mention_count = len({m.id for m in message.mentions}) + len(message.role_mentions)
            rule, to_delete = self.spam.check(
                message.author.id, message.channel.id, message.id, message.content, mention_count,
            )
            if rule is None:
                return
            await self._delete_messages(message.guild, to_delete)
            strike, timeout = self.strikes.add(message.author.id)
            if timeout is None:
                await message.channel.send(
                    f"{message.author.mention} slow down, that looked like spam ({rule.replace('_', ' ')}). "
                    "Next time is a timeout.",
                    delete_after=10,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            else:
                await message.author.timeout(timeout, reason=f"{REASON}: spam ({rule}), strike {strike}")
        except discord.HTTPException:
            pass
        except Exception:
            traceback.print_exc()

    async def _delete_messages(self, guild, to_delete):
        by_channel = defaultdict(list)
        for channel_id, message_id in to_delete:
            by_channel[channel_id].append(message_id)
        for channel_id, message_ids in by_channel.items():
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            try:
                await channel.delete_messages([channel.get_partial_message(mid) for mid in message_ids], reason=f"{REASON}: spam")
            except discord.HTTPException:
                pass


async def setup(bot):
    await bot.add_cog(AntiRaid(bot))
