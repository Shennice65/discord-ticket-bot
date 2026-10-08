import asyncio
import base64
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from config import Config
from utils.admin_alerts import send_security_alert
from utils.security.journal import ActionJournal
from utils.security.snapshot import CATEGORY_TYPE, order_for_restore, remap_overwrites, serialize_channel, serialize_role
from utils.security.tracker import ActionTracker, classify

QUARANTINE_ROLE_NAME = "Quarantined"
QUARANTINE_TIMEOUT = timedelta(days=27, hours=23)
SETTLE_SECONDS = 2  # let in-flight delete events land before reverting
QUIET_SECONDS = 5  # incident is finished once the attacker has been quiet this long
DELETED_CACHE_SECONDS = 10 * 60
BACKUP_TRIM_EVERY = 25
REASON = "Anti-nuke"


def release_view(incident_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Release user (false positive)",
        style=discord.ButtonStyle.danger,
        custom_id=f"security_release:{incident_id}",
    ))
    return view


def _clip(lines, limit=1024) -> str:
    text = ""
    for line in lines:
        if len(text) + len(line) + 1 > limit - 20:
            return text + f"…and {len(lines) - text.count(chr(10))} more"
        text += line + "\n"
    return text or "—"


class RevertReport:
    def __init__(self, id_map=None):
        self.restored = []
        self.failed = []
        self.notes = []
        self.id_map = dict(id_map or {})  # old id -> new id
        self.names = {}  # new id -> display name
        self.role_positions = []  # (role, position)
        self.role_members = []  # (role, member_ids)
        self.role_overwrites = []  # (role, channel_id, allow, deny)
        self.recreated_channels = []  # (old_id, new_channel)


class AntiNuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tracker = ActionTracker()
        self.journal = ActionJournal()
        self.deleted_channels = {}  # id -> (monotonic, data)
        self.deleted_roles = {}
        self.role_members = defaultdict(set)
        self.active = {}  # actor_id -> incident state
        self.incident_lock = asyncio.Lock()
        self.backup_counts = Counter()

    async def cog_load(self):
        self.snapshot_loop.start()

    async def cog_unload(self):
        self.snapshot_loop.cancel()

    @staticmethod
    def is_protected_guild(guild) -> bool:
        return guild is not None and (not Config.GUILD_ID or guild.id == Config.GUILD_ID)

    # ------------------------------------------------------------------ caches

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            if self.is_protected_guild(guild):
                self._build_role_members(guild)

    def _build_role_members(self, guild):
        self.role_members.clear()
        for member in guild.members:
            for role in member.roles:
                if not role.is_default():
                    self.role_members[role.id].add(member.id)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if not self.is_protected_guild(after.guild):
            return
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        for role_id in after_ids - before_ids:
            self.role_members[role_id].add(after.id)
        for role_id in before_ids - after_ids:
            # A deleted role vanishes from members too; keep it so the role can be restored with its members.
            if after.guild.get_role(role_id) is not None:
                self.role_members[role_id].discard(after.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if self.is_protected_guild(channel.guild):
            self._prune_deleted()
            self.deleted_channels[channel.id] = (time.monotonic(), serialize_channel(channel))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        if not self.is_protected_guild(role.guild):
            return
        self._prune_deleted()
        data = serialize_role(role, self.role_members.get(role.id))
        data["channel_overwrites"] = [
            {"channel_id": channel.id, "allow": int(ow.allow), "deny": int(ow.deny)}
            for channel in role.guild.channels
            for ow in getattr(channel, "_overwrites", [])
            if ow.id == role.id
        ]
        self.deleted_roles[role.id] = (time.monotonic(), data)

    def _prune_deleted(self):
        cutoff = time.monotonic() - DELETED_CACHE_SECONDS
        for cache in (self.deleted_channels, self.deleted_roles):
            for key in [k for k, (ts, _) in cache.items() if ts < cutoff]:
                del cache[key]

    async def _wait_for_deleted(self, cache, object_id):
        for _ in range(15):
            if object_id in cache:
                return cache[object_id][1]
            await asyncio.sleep(0.1)
        return None

    # --------------------------------------------------------------- detection

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        guild = entry.guild
        if not self.is_protected_guild(guild):
            return
        actor_id = entry.user_id
        if actor_id is None or actor_id == self.bot.user.id or actor_id == guild.owner_id:
            return
        if actor_id in await self.bot.db.get_security_whitelist():
            return

        try:
            record, classify_args = await self._record_from_entry(guild, entry)
            if record is None:
                return

            state = self.active.get(actor_id)
            if state is not None:
                state["pending"].append(record)
                return

            self.journal.add(actor_id, record)
            bucket, instant = classify(record["action"], **classify_args)
            if instant:
                await self._start_incident(guild, actor_id, f"Instant trigger: {record['action']}")
            elif bucket and self.tracker.record(actor_id, bucket):
                await self._start_incident(guild, actor_id, f"Threshold reached: {bucket}")
        except Exception:
            traceback.print_exc()

    async def _record_from_entry(self, guild, entry):
        action = entry.action.name
        target_id = getattr(entry.target, "id", None) or getattr(entry, "_target_id", None)
        before, after = entry.before, entry.after
        record = {
            "action": action,
            "actor_id": entry.user_id,
            "target_id": target_id,
            "at": datetime.now(timezone.utc),
            "data": {},
        }
        classify_args = {}

        if action == "channel_delete":
            data = await self._wait_for_deleted(self.deleted_channels, target_id) or {}
            if Config.TICKET_CATEGORY_ID and data.get("category_id") == Config.TICKET_CATEGORY_ID:
                return None, None  # staff closing ticket channels by hand is normal
            record["data"] = data
        elif action == "role_delete":
            record["data"] = await self._wait_for_deleted(self.deleted_roles, target_id) or {}
        elif action in ("role_update", "role_create"):
            before_perms = getattr(before, "permissions", None)
            after_perms = getattr(after, "permissions", None)
            classify_args = {
                "before_perms": before_perms.value if before_perms is not None else 0,
                "after_perms": after_perms.value if after_perms is not None else None,
            }
            if action == "role_update":
                colour = getattr(before, "colour", None)
                record["data"] = {
                    "name": getattr(before, "name", None),
                    "permissions": before_perms.value if before_perms is not None else None,
                    "color": colour.value if colour is not None else None,
                    "hoist": getattr(before, "hoist", None),
                    "mentionable": getattr(before, "mentionable", None),
                }
        elif action in ("overwrite_create", "overwrite_update", "overwrite_delete"):
            extra = entry.extra
            if extra is None:
                return record, classify_args
            is_role = isinstance(extra, discord.Role) or getattr(extra, "type", None) is discord.Role
            before_allow = getattr(before, "allow", None)
            before_deny = getattr(before, "deny", None)
            after_allow = getattr(after, "allow", None)
            record["data"] = {
                "channel_id": target_id,
                "ow_id": extra.id,
                "ow_type": 0 if is_role else 1,
                "before": None if action == "overwrite_create" or before_allow is None
                else [before_allow.value, before_deny.value if before_deny is not None else 0],
            }
            classify_args = {
                "target_is_everyone": extra.id == guild.id,
                "before_perms": before_allow.value if before_allow is not None else 0,
                "after_perms": after_allow.value if after_allow is not None else None,
            }
        elif action == "member_role_update":
            added = [r.id for r in (getattr(after, "roles", None) or [])]
            removed = [r.id for r in (getattr(before, "roles", None) or [])]
            record["data"] = {"added": added, "removed": removed}
            classify_args = {
                "added_role_perms": [guild.get_role(rid).permissions.value for rid in added if guild.get_role(rid)],
            }
        elif action == "guild_update":
            level = getattr(before, "verification_level", None)
            record["data"] = {
                "name": getattr(before, "name", None),
                "verification_level": level.value if level is not None else None,
                "vanity": getattr(before, "vanity_url_code", None),
                "vanity_changed": hasattr(before, "vanity_url_code"),
                "icon_changed": hasattr(before, "icon"),
            }
            classify_args = {"vanity_changed": hasattr(before, "vanity_url_code")}
        elif action == "channel_update":
            record["data"] = {
                key: getattr(before, key)
                for key in ("name", "topic", "nsfw", "slowmode_delay")
                if hasattr(before, key)
            }
        return record, classify_args

    # ------------------------------------------------------------- containment

    async def _start_incident(self, guild, actor_id, reason):
        async with self.incident_lock:
            if actor_id in self.active:
                return
            state = {"pending": [], "incident_id": None}
            self.active[actor_id] = state

        try:
            entries = self.journal.take(actor_id)
            self.tracker.reset(actor_id)
            started_at = datetime.now(timezone.utc)
            containment = await self._contain(guild, actor_id)
            incident_id = await self.bot.db.create_security_incident({
                "guild_id": guild.id,
                "actor_id": actor_id,
                "reason": reason,
                "status": "reverting",
                "containment": containment,
                "actions": entries,
                "started_at": started_at,
            })
            state["incident_id"] = incident_id
        except Exception:
            self.active.pop(actor_id, None)
            raise
        asyncio.create_task(self._run_incident(guild, actor_id, state, entries, containment, reason, started_at))

    async def _contain(self, guild, actor_id) -> dict:
        member = guild.get_member(actor_id)
        if member is None:
            try:
                member = await guild.fetch_member(actor_id)
            except discord.HTTPException:
                return {"status": "not_in_server"}

        me = guild.me
        if member.id == guild.owner_id or member.top_role >= me.top_role:
            return {"status": "above_bot"}

        if member.bot:
            try:
                await member.kick(reason=f"{REASON}: malicious bot")
                return {"status": "kicked_bot"}
            except discord.HTTPException as error:
                return {"status": "failed", "error": str(error)}

        stripped = [r.id for r in member.roles if not r.is_default() and not r.managed]
        kept = [r for r in member.roles if not r.is_default() and r.managed]
        result = {"status": "quarantined", "stripped_roles": stripped, "timeout": False}
        try:
            quarantine = await self._quarantine_role(guild)
            await member.edit(roles=kept + [quarantine], reason=f"{REASON}: quarantine")
        except discord.HTTPException as error:
            return {"status": "failed", "error": str(error), "stripped_roles": stripped}
        try:
            await member.timeout(QUARANTINE_TIMEOUT, reason=f"{REASON}: quarantine")
            result["timeout"] = True
        except discord.HTTPException:
            pass
        return result

    async def _quarantine_role(self, guild):
        role = discord.utils.get(guild.roles, name=QUARANTINE_ROLE_NAME)
        if role is None:
            role = await guild.create_role(
                name=QUARANTINE_ROLE_NAME, permissions=discord.Permissions.none(), reason=f"{REASON}: quarantine role",
            )
        return role

    # ------------------------------------------------------------------ revert

    async def _run_incident(self, guild, actor_id, state, entries, containment, reason, started_at):
        report = RevertReport()
        all_entries = list(entries)
        try:
            await asyncio.sleep(SETTLE_SECONDS)
            await self.revert_entries(guild, entries, report)

            quiet_since = time.monotonic()
            while time.monotonic() - quiet_since < QUIET_SECONDS:
                await asyncio.sleep(1)
                if state["pending"]:
                    batch, state["pending"] = state["pending"], []
                    all_entries.extend(batch)
                    await asyncio.sleep(SETTLE_SECONDS)
                    await self.revert_entries(guild, batch, report)
                    quiet_since = time.monotonic()
        except Exception as error:
            traceback.print_exc()
            report.failed.append(f"Revert crashed: {error}")
        finally:
            self.active.pop(actor_id, None)

        incident_id = state["incident_id"]
        await self._save_report(incident_id, report, all_entries, "reverted")
        embed = self.incident_embed(incident_id, actor_id, reason, containment, all_entries, report)
        view = release_view(incident_id) if containment.get("status") == "quarantined" else None
        await send_security_alert(self.bot, embed=embed, view=view, guild=guild)

        await self.finish_slow_steps(guild, report, started_at - timedelta(seconds=60))
        await self._save_report(incident_id, report, all_entries, "done")
        if report.role_members or report.recreated_channels:
            follow_up = discord.Embed(
                title=f"Incident #{incident_id}: follow-up finished",
                description=_clip(report.notes[-15:] or ["Member roles and message replay finished."], 4000),
                color=discord.Color.green(),
            )
            await send_security_alert(self.bot, embed=follow_up, guild=guild)

    async def _save_report(self, incident_id, report, entries, status):
        await self.bot.db.update_security_incident(incident_id, {
            "status": status,
            "actions": entries,
            "restored": report.restored,
            "failed": report.failed,
            "notes": report.notes,
            "id_map": {str(k): v for k, v in report.id_map.items()},
        })

    async def revert_entries(self, guild, entries, report: RevertReport):
        webhooks = None
        for entry in order_for_restore(entries):
            try:
                if entry["action"] == "webhook_create" and webhooks is None:
                    webhooks = await guild.webhooks()
                await self._revert_one(guild, entry, report, webhooks or [])
            except discord.HTTPException as error:
                report.failed.append(f"{entry['action']} {entry.get('target_id')}: {error}")
            except Exception as error:
                traceback.print_exc()
                report.failed.append(f"{entry['action']} {entry.get('target_id')}: {error}")

        me_top = guild.me.top_role.position
        if report.role_positions:
            positions = {role: max(1, min(pos, me_top - 1)) for role, pos in report.role_positions}
            report.role_positions = []
            try:
                await guild.edit_role_positions(positions, reason=f"{REASON}: restore role order")
            except discord.HTTPException as error:
                report.failed.append(f"Role positions: {error}")

        pending_overwrites, report.role_overwrites = report.role_overwrites, []
        for role, channel_id, allow, deny in pending_overwrites:
            channel = guild.get_channel(report.id_map.get(channel_id, channel_id))
            if channel is None:
                continue
            try:
                overwrite = discord.PermissionOverwrite.from_pair(discord.Permissions(allow), discord.Permissions(deny))
                await channel.set_permissions(role, overwrite=overwrite, reason=f"{REASON}: restore role overwrite")
            except discord.HTTPException as error:
                report.failed.append(f"Overwrite for {role.name} in #{channel.name}: {error}")

    async def _latest_snapshot_role(self, role_id):
        snapshot = await self.bot.db.get_security_snapshot()
        if not snapshot:
            return None
        return next((r for r in snapshot.get("roles", []) if r["id"] == role_id), None)

    async def _revert_one(self, guild, entry, report: RevertReport, webhooks):
        action, target_id, data = entry["action"], entry.get("target_id"), entry.get("data") or {}
        reason = f"{REASON}: revert {action}"

        if action == "role_delete":
            existing = report.id_map.get(target_id)
            if existing and guild.get_role(existing):
                return
            snapshot_role = await self._latest_snapshot_role(target_id)
            if not data:
                data = snapshot_role
            if not data:
                report.failed.append(f"Role {target_id}: no saved data")
                return
            role = await guild.create_role(
                name=data["name"],
                permissions=discord.Permissions(data["permissions"]),
                colour=discord.Colour(data["color"]),
                hoist=data["hoist"],
                mentionable=data["mentionable"],
                reason=reason,
            )
            report.id_map[target_id] = role.id
            report.names[role.id] = f"@{role.name}"
            report.restored.append(f"Recreated role @{role.name}")
            report.role_positions.append((role, data.get("position", 1)))
            members = data.get("members") or (snapshot_role or {}).get("members") or []
            if members:
                report.role_members.append((role, members))
            overwrites = data.get("channel_overwrites")
            if not overwrites:
                snapshot = await self.bot.db.get_security_snapshot()
                overwrites = [
                    {"channel_id": ch["id"], "allow": ow["allow"], "deny": ow["deny"]}
                    for ch in (snapshot or {}).get("channels", [])
                    for ow in ch.get("overwrites", [])
                    if ow["id"] == target_id
                ]
            for ow in overwrites:
                report.role_overwrites.append((role, ow["channel_id"], ow["allow"], ow["deny"]))

        elif action == "channel_delete":
            existing = report.id_map.get(target_id)
            if existing and guild.get_channel(existing):
                return
            if not data:
                snapshot = await self.bot.db.get_security_snapshot()
                data = next((c for c in (snapshot or {}).get("channels", []) if c["id"] == target_id), None)
            if not data:
                report.failed.append(f"Channel {target_id}: no saved data")
                return
            channel = await self._create_channel(guild, data, report.id_map, reason)
            report.id_map[target_id] = channel.id
            report.names[channel.id] = f"#{channel.name}"
            report.restored.append(f"Recreated #{channel.name}")
            report.recreated_channels.append((target_id, channel))
            if data["type"] == CATEGORY_TYPE:
                for child_id in data.get("children", []):
                    child = guild.get_channel(child_id)
                    if child is not None and (child.category_id is None or child.category_id == target_id):
                        await child.edit(category=channel, reason=reason)

        elif action == "ban":
            await guild.unban(discord.Object(id=target_id), reason=reason)
            report.restored.append(f"Unbanned <@{target_id}>")

        elif action == "kick":
            report.notes.append(f"<@{target_id}> was kicked (cannot be undone, re-invite them)")

        elif action == "member_prune":
            report.notes.append("Members were pruned (cannot be undone)")

        elif action == "channel_create":
            channel = guild.get_channel(target_id)
            if channel is not None:
                await channel.delete(reason=reason)
                report.restored.append(f"Deleted attacker channel #{channel.name}")

        elif action == "role_create":
            role = guild.get_role(target_id)
            if role is not None and role < guild.me.top_role:
                await role.delete(reason=reason)
                report.restored.append(f"Deleted attacker role @{role.name}")

        elif action == "webhook_create":
            webhook = next((w for w in webhooks if w.id == target_id), None)
            if webhook is not None:
                await webhook.delete(reason=reason)
                report.restored.append(f"Deleted attacker webhook {webhook.name}")

        elif action == "bot_add":
            member = guild.get_member(target_id)
            if member is not None:
                await member.kick(reason=reason)
                report.restored.append(f"Kicked bot {member}")

        elif action == "role_update":
            role = guild.get_role(target_id)
            if role is None or role >= guild.me.top_role:
                return
            kwargs = {}
            if data.get("permissions") is not None:
                kwargs["permissions"] = discord.Permissions(data["permissions"])
            if data.get("name") is not None:
                kwargs["name"] = data["name"]
            if data.get("color") is not None:
                kwargs["colour"] = discord.Colour(data["color"])
            for key in ("hoist", "mentionable"):
                if data.get(key) is not None:
                    kwargs[key] = data[key]
            if kwargs:
                await role.edit(**kwargs, reason=reason)
                report.restored.append(f"Reverted changes to @{role.name}")

        elif action in ("overwrite_create", "overwrite_update", "overwrite_delete"):
            channel_id = data.get("channel_id")
            channel = guild.get_channel(report.id_map.get(channel_id, channel_id))
            if channel is None or "ow_id" not in data:
                return
            ow_id = report.id_map.get(data["ow_id"], data["ow_id"])
            target = guild.get_role(ow_id) if data["ow_type"] == 0 else guild.get_member(ow_id)
            if target is None:
                target = discord.Object(id=ow_id, type=discord.Role if data["ow_type"] == 0 else discord.Member)
            before = data.get("before")
            overwrite = None if before is None else discord.PermissionOverwrite.from_pair(
                discord.Permissions(before[0]), discord.Permissions(before[1]),
            )
            await channel.set_permissions(target, overwrite=overwrite, reason=reason)
            report.restored.append(f"Reverted permission overwrite in #{channel.name}")

        elif action == "channel_update":
            channel = guild.get_channel(target_id)
            if channel is not None and data:
                await channel.edit(**data, reason=reason)
                report.restored.append(f"Reverted edits to #{channel.name}")

        elif action == "member_role_update":
            member = guild.get_member(target_id)
            if member is None:
                return
            me_top = guild.me.top_role
            to_remove = [r for r in (guild.get_role(i) for i in data.get("added", [])) if r and r < me_top]
            to_add = [r for r in (guild.get_role(report.id_map.get(i, i)) for i in data.get("removed", [])) if r and r < me_top]
            if to_remove:
                await member.remove_roles(*to_remove, reason=reason)
            if to_add:
                await member.add_roles(*to_add, reason=reason)
            if to_remove or to_add:
                report.restored.append(f"Reverted role changes on {member}")

        elif action == "guild_update":
            kwargs = {}
            if data.get("name"):
                kwargs["name"] = data["name"]
            if data.get("verification_level") is not None:
                kwargs["verification_level"] = discord.VerificationLevel(data["verification_level"])
            if data.get("icon_changed"):
                snapshot = await self.bot.db.get_security_snapshot()
                icon = (snapshot or {}).get("guild", {}).get("icon_b64")
                kwargs["icon"] = base64.b64decode(icon) if icon else None
            if kwargs:
                await guild.edit(**kwargs, reason=reason)
                report.restored.append("Reverted server settings")
            if data.get("vanity_changed") and data.get("vanity"):
                try:
                    await guild.edit(vanity_code=data["vanity"], reason=reason)
                    report.restored.append(f"Restored vanity URL {data['vanity']}")
                except discord.HTTPException as error:
                    report.failed.append(f"Vanity URL: {error}")

        else:
            report.notes.append(f"{action} on {target_id} was not reverted")

    def _build_overwrites(self, guild, overwrites, id_map):
        result = {}
        for ow in remap_overwrites(overwrites, id_map):
            target = guild.get_role(ow["id"]) if ow["type"] == 0 else guild.get_member(ow["id"])
            if target is None:
                continue
            result[target] = discord.PermissionOverwrite.from_pair(
                discord.Permissions(ow["allow"]), discord.Permissions(ow["deny"]),
            )
        return result

    async def _create_channel(self, guild, data, id_map, reason):
        channel_type = data["type"]
        kwargs = {
            "overwrites": self._build_overwrites(guild, data.get("overwrites", []), id_map),
            "position": data.get("position", 0),
            "reason": reason,
        }
        if channel_type == CATEGORY_TYPE:
            return await guild.create_category(data["name"], **kwargs)

        category_id = data.get("category_id")
        category = guild.get_channel(id_map.get(category_id, category_id)) if category_id else None
        if isinstance(category, discord.CategoryChannel):
            kwargs["category"] = category

        if channel_type == discord.ChannelType.voice.value:
            if data.get("bitrate"):
                kwargs["bitrate"] = min(data["bitrate"], int(guild.bitrate_limit))
            if data.get("user_limit") is not None:
                kwargs["user_limit"] = data["user_limit"]
            return await guild.create_voice_channel(data["name"], **kwargs)
        if channel_type == discord.ChannelType.stage_voice.value:
            return await guild.create_stage_channel(data["name"], **kwargs)

        if data.get("topic"):
            kwargs["topic"] = data["topic"]
        kwargs["nsfw"] = data.get("nsfw", False)
        kwargs["slowmode_delay"] = data.get("slowmode_delay", 0)
        if channel_type == discord.ChannelType.forum.value:
            return await guild.create_forum(data["name"], **kwargs)
        if channel_type == discord.ChannelType.news.value:
            kwargs["news"] = True
        return await guild.create_text_channel(data["name"], **kwargs)

    async def finish_slow_steps(self, guild, report: RevertReport, replay_since):
        """Re-add members to recreated roles and replay backed-up messages."""
        pending, report.role_members = report.role_members, []
        for role, member_ids in pending:
            added = 0
            for member_id in member_ids:
                member = guild.get_member(member_id)
                if member is None or role in member.roles:
                    continue
                try:
                    await member.add_roles(role, reason=f"{REASON}: restore role members")
                    added += 1
                except discord.HTTPException:
                    pass
            report.notes.append(f"Gave @{role.name} back to {added} member(s)")

        backup_channels = await self.bot.db.get_backup_channels()
        for old_id, channel in report.recreated_channels:
            if old_id not in backup_channels or not hasattr(channel, "create_webhook"):
                continue
            sent = await self.replay_backup(old_id, channel, replay_since)
            report.notes.append(f"Replayed {sent} message(s) into #{channel.name}")

    async def replay_backup(self, old_id, channel, since) -> int:
        db = self.bot.db
        messages = await db.get_backed_up_messages(old_id, since)
        await db.delete_message_backup(old_id)
        channels = await db.get_backup_channels()
        await db.set_backup_channels((channels - {old_id}) | {channel.id})

        webhook = await channel.create_webhook(name="Restore", reason=f"{REASON}: message replay")
        sent = 0
        try:
            for message in messages:
                content = message.get("content") or ""
                if message.get("attachments"):
                    content += "\n" + " ".join(f"📎 {name}" for name in message["attachments"])
                embeds = [discord.Embed.from_dict(e) for e in message.get("embeds", [])][:10]
                if not content.strip() and not embeds:
                    continue
                kwargs = {
                    "username": (message.get("author_name") or "Restored message")[:80],
                    "avatar_url": message.get("avatar_url"),
                    "allowed_mentions": discord.AllowedMentions.none(),
                    "embeds": embeds,
                }
                if content.strip():
                    kwargs["content"] = content[:2000]
                try:
                    await webhook.send(**kwargs)
                except discord.HTTPException:
                    kwargs["username"] = "Restored message"  # some names are rejected by webhooks
                    await webhook.send(**kwargs)
                sent += 1
        finally:
            await webhook.delete(reason=f"{REASON}: message replay done")
        return sent

    # ---------------------------------------------------------------- reports

    @staticmethod
    def incident_embed(incident_id, actor_id, reason, containment, entries, report: RevertReport):
        status = containment.get("status")
        status_text = {
            "quarantined": "Roles stripped, quarantined" + (" and timed out" if containment.get("timeout") else ""),
            "kicked_bot": "Bot kicked",
            "not_in_server": "Attacker already left the server",
            "above_bot": "⚠️ COULD NOT ACT: attacker's role is above the bot. Move the bot role to the top NOW and remove their roles manually.",
            "failed": f"⚠️ Containment failed: {containment.get('error')}",
        }.get(status, status)
        urgent = status in ("above_bot", "failed")
        embed = discord.Embed(
            title=f"{'🚨 URGENT ' if urgent else '🛡️ '}Anti-nuke incident #{incident_id}",
            description=f"**Attacker:** <@{actor_id}> (`{actor_id}`)\n**Trigger:** {reason}\n**Action taken:** {status_text}",
            color=discord.Color.red() if urgent else discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        counts = Counter(e["action"] for e in entries)
        embed.add_field(name="Detected actions", value=_clip([f"{k}: {v}" for k, v in counts.most_common()]), inline=False)
        embed.add_field(name="Restored", value=_clip(report.restored), inline=False)
        if report.failed:
            embed.add_field(name="Failed", value=_clip(report.failed), inline=False)
        if report.notes:
            embed.add_field(name="Notes", value=_clip(report.notes), inline=False)
        if report.id_map:
            lines = [f"{old} -> {new} {report.names.get(new, '')}" for old, new in report.id_map.items()]
            embed.add_field(
                name="New IDs (update .env if any were configured)",
                value="```\n" + _clip(lines, 1000 - 8) + "```",
                inline=False,
            )
        return embed

    async def release(self, guild, incident_id) -> str:
        incident = await self.bot.db.get_security_incident(incident_id)
        if incident is None:
            return "Incident not found."
        if incident.get("status") == "released":
            return "Already released."
        member = guild.get_member(incident["actor_id"])
        if member is None:
            return "That user is no longer in the server."
        id_map = {int(k): v for k, v in (incident.get("id_map") or {}).items()}
        me_top = guild.me.top_role
        roles = [guild.get_role(id_map.get(rid, rid)) for rid in incident.get("containment", {}).get("stripped_roles", [])]
        roles = [r for r in roles if r is not None and r < me_top]
        kept = [r for r in member.roles if not r.is_default() and r.managed]
        await member.edit(roles=kept + roles, timed_out_until=None, reason=f"{REASON}: released incident #{incident_id}")
        await self.bot.db.update_security_incident(incident_id, {"status": "released"})
        return f"Released {member.mention} and restored {len(roles)} role(s)."

    # --------------------------------------------------------------- snapshots

    @tasks.loop(minutes=30)
    async def snapshot_loop(self):
        for guild in self.bot.guilds:
            if self.is_protected_guild(guild):
                try:
                    await self.take_snapshot(guild)
                except Exception:
                    traceback.print_exc()

    @snapshot_loop.before_loop
    async def before_snapshot_loop(self):
        await self.bot.wait_until_ready()

    async def take_snapshot(self, guild) -> int:
        icon_b64 = None
        if guild.icon is not None:
            icon_b64 = base64.b64encode(await guild.icon.read()).decode()
        return await self.bot.db.save_security_snapshot({
            "guild_id": guild.id,
            "roles": [
                serialize_role(role, [m.id for m in role.members])
                for role in guild.roles
                if not role.is_default() and not role.managed
            ],
            "channels": [serialize_channel(channel) for channel in guild.channels],
            "guild": {
                "name": guild.name,
                "verification_level": guild.verification_level.value,
                "vanity": guild.vanity_url_code,
                "icon_b64": icon_b64,
            },
        })

    def snapshot_diff(self, guild, snapshot):
        missing_roles = [r for r in snapshot.get("roles", []) if guild.get_role(r["id"]) is None]
        missing_channels = [c for c in snapshot.get("channels", []) if guild.get_channel(c["id"]) is None]
        return missing_roles, missing_channels

    async def restore_snapshot(self, guild, snapshot) -> RevertReport:
        missing_roles, missing_channels = self.snapshot_diff(guild, snapshot)
        entries = [{"action": "role_delete", "target_id": r["id"], "data": r} for r in missing_roles]
        entries += [{"action": "channel_delete", "target_id": c["id"], "data": c} for c in missing_channels]
        report = RevertReport()
        await self.revert_entries(guild, entries, report)
        await self.finish_slow_steps(guild, report, snapshot["created_at"])
        return report

    # ---------------------------------------------------------- message backup

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or not self.is_protected_guild(message.guild):
            return
        if message.channel.id not in await self.bot.db.get_backup_channels():
            return
        await self.backup_message(message)
        self.backup_counts[message.channel.id] += 1
        if self.backup_counts[message.channel.id] % BACKUP_TRIM_EVERY == 0:
            await self.bot.db.trim_message_backup(message.channel.id)

    async def backup_message(self, message):
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        await self.bot.db.backup_message({
            "message_id": message.id,
            "channel_id": message.channel.id,
            "author_name": message.author.display_name,
            "avatar_url": str(message.author.display_avatar.url),
            "content": message.content,
            "embeds": [e.to_dict() for e in message.embeds if e.type == "rich"],
            "attachments": [a.filename for a in message.attachments],
            "created_at": message.created_at,
            "deleted_at": None,
        })

    async def backfill_channel(self, channel) -> int:
        count = 0
        async for message in channel.history(limit=500):
            await self.backup_message(message)
            count += 1
        await self.bot.db.trim_message_backup(channel.id)
        return count

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        if payload.channel_id in await self.bot.db.get_backup_channels() and "content" in payload.data:
            await self.bot.db.edit_backed_up_message(payload.message_id, payload.data["content"])

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        if payload.channel_id in await self.bot.db.get_backup_channels():
            await self.bot.db.mark_backed_up_message_deleted([payload.message_id])

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        if payload.channel_id in await self.bot.db.get_backup_channels():
            await self.bot.db.mark_backed_up_message_deleted(payload.message_ids)


async def setup(bot):
    await bot.add_cog(AntiNuke(bot))
