import functools
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.security.antinuke import AntiNuke, RevertReport
from cogs.security.antiraid import AntiRaid

GUILD_ID = 1000
OWNER_ID = 1
BOT_ID = 2
ATTACKER_ID = 66


@functools.total_ordering
class FakeRole:
    def __init__(self, role_id, position, *, managed=False, default=False, name="role"):
        self.id = role_id
        self.position = position
        self.managed = managed
        self._default = default
        self.name = name

    def is_default(self):
        return self._default

    def __eq__(self, other):
        return isinstance(other, FakeRole) and self.id == other.id

    def __lt__(self, other):
        return self.position < other.position

    def __hash__(self):
        return hash(self.id)


class GuildTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("config.Config.GUILD_ID", GUILD_ID)
        patcher.start()
        self.addCleanup(patcher.stop)


def make_db(**overrides):
    db = SimpleNamespace(
        get_security_whitelist=AsyncMock(return_value=set()),
        get_backup_channels=AsyncMock(return_value=set()),
        get_security_snapshot=AsyncMock(return_value=None),
        create_security_incident=AsyncMock(return_value=1),
        get_lockdown=AsyncMock(return_value=None),
        save_lockdown=AsyncMock(),
        clear_lockdown=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(db, key, value)
    return db


def make_entry(action, target_id, actor_id=ATTACKER_ID, guild=None):
    return SimpleNamespace(
        guild=guild,
        user_id=actor_id,
        action=SimpleNamespace(name=action),
        target=SimpleNamespace(id=target_id),
        before=SimpleNamespace(),
        after=SimpleNamespace(),
        extra=None,
    )


class AntiNukeDetectionTests(GuildTestCase):
    def setUp(self):
        super().setUp()
        self.guild = SimpleNamespace(id=GUILD_ID, owner_id=OWNER_ID)
        self.bot = SimpleNamespace(user=SimpleNamespace(id=BOT_ID), db=make_db())
        self.cog = AntiNuke(self.bot)
        self.cog._start_incident = AsyncMock()
        for channel_id in (10, 11, 12):
            self.cog.deleted_channels[channel_id] = (10**12, {"id": channel_id, "type": 0, "category_id": None})

    async def delete_three(self, actor_id):
        for channel_id in (10, 11, 12):
            await self.cog.on_audit_log_entry_create(make_entry("channel_delete", channel_id, actor_id, self.guild))

    async def test_three_channel_deletes_start_incident(self):
        await self.delete_three(ATTACKER_ID)
        self.cog._start_incident.assert_awaited_once()
        self.assertEqual(self.cog._start_incident.await_args.args[1], ATTACKER_ID)

    async def test_owner_bot_and_whitelisted_are_ignored(self):
        self.bot.db.get_security_whitelist.return_value = {ATTACKER_ID}
        for actor in (OWNER_ID, BOT_ID, ATTACKER_ID):
            await self.delete_three(actor)
        self.cog._start_incident.assert_not_awaited()

    async def test_ticket_category_deletions_are_ignored(self):
        for channel_id in (10, 11, 12):
            self.cog.deleted_channels[channel_id][1]["category_id"] = 555
        with patch("cogs.security.antinuke.Config.TICKET_CATEGORY_ID", 555):
            await self.delete_three(ATTACKER_ID)
        self.cog._start_incident.assert_not_awaited()

    async def test_actions_during_incident_are_queued_for_revert(self):
        state = {"pending": [], "incident_id": 1}
        self.cog.active[ATTACKER_ID] = state
        await self.cog.on_audit_log_entry_create(make_entry("ban", 77, ATTACKER_ID, self.guild))
        self.assertEqual([e["action"] for e in state["pending"]], ["ban"])
        self.cog._start_incident.assert_not_awaited()


class ContainTests(unittest.IsolatedAsyncioTestCase):
    def make_guild(self, member):
        quarantine = FakeRole(500, 1, name="Quarantined")
        return SimpleNamespace(
            id=GUILD_ID,
            owner_id=OWNER_ID,
            me=SimpleNamespace(top_role=FakeRole(900, 50)),
            roles=[quarantine],
            get_member=lambda member_id: member,
            create_role=AsyncMock(),
        ), quarantine

    async def test_member_is_stripped_quarantined_and_timed_out(self):
        everyone = FakeRole(GUILD_ID, 0, default=True)
        admin = FakeRole(10, 20, name="Admin")
        booster = FakeRole(11, 5, managed=True)
        member = SimpleNamespace(
            id=ATTACKER_ID, bot=False, roles=[everyone, admin, booster], top_role=admin,
            edit=AsyncMock(), timeout=AsyncMock(), kick=AsyncMock(),
        )
        guild, quarantine = self.make_guild(member)
        cog = AntiNuke(SimpleNamespace(db=make_db()))

        result = await cog._contain(guild, ATTACKER_ID)

        self.assertEqual(result, {"status": "quarantined", "stripped_roles": [10], "timeout": True})
        self.assertEqual(member.edit.await_args.kwargs["roles"], [booster, quarantine])
        member.timeout.assert_awaited_once()

    async def test_bot_attacker_is_kicked(self):
        member = SimpleNamespace(id=ATTACKER_ID, bot=True, roles=[], top_role=FakeRole(3, 3), kick=AsyncMock())
        guild, _ = self.make_guild(member)
        result = await AntiNuke(SimpleNamespace(db=make_db()))._contain(guild, ATTACKER_ID)
        self.assertEqual(result["status"], "kicked_bot")
        member.kick.assert_awaited_once()

    async def test_attacker_above_bot_cannot_be_touched(self):
        member = SimpleNamespace(id=ATTACKER_ID, bot=False, roles=[], top_role=FakeRole(3, 99), edit=AsyncMock())
        guild, _ = self.make_guild(member)
        result = await AntiNuke(SimpleNamespace(db=make_db()))._contain(guild, ATTACKER_ID)
        self.assertEqual(result["status"], "above_bot")
        member.edit.assert_not_awaited()


class RevertTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_recreated_before_channel_and_overwrites_remapped(self):
        calls = []
        roles = {}
        new_role = FakeRole(901, 1, name="Mods")
        new_channel = SimpleNamespace(id=902, name="mod-chat")

        async def create_role(**kwargs):
            calls.append("create_role")
            roles[new_role.id] = new_role
            return new_role

        async def create_text_channel(name, **kwargs):
            calls.append("create_text_channel")
            self.channel_kwargs = kwargs
            return new_channel

        guild = SimpleNamespace(
            id=GUILD_ID,
            me=SimpleNamespace(top_role=FakeRole(999, 50)),
            get_role=lambda role_id: roles.get(role_id),
            get_channel=lambda channel_id: None,
            get_member=lambda member_id: None,
            create_role=create_role,
            create_text_channel=create_text_channel,
            edit_role_positions=AsyncMock(),
        )
        entries = [
            {"action": "channel_delete", "target_id": 20, "data": {
                "id": 20, "type": 0, "name": "mod-chat", "position": 3, "category_id": None, "topic": "staff",
                "nsfw": False, "slowmode_delay": 0,
                "overwrites": [{"id": 10, "type": 0, "allow": 1024, "deny": 0}],
            }},
            {"action": "role_delete", "target_id": 10, "data": {
                "id": 10, "name": "Mods", "color": 0, "hoist": True, "mentionable": False,
                "permissions": 8, "position": 7, "members": [5, 6], "channel_overwrites": [],
            }},
        ]
        report = RevertReport()
        cog = AntiNuke(SimpleNamespace(db=make_db()))

        await cog.revert_entries(guild, entries, report)

        self.assertEqual(calls, ["create_role", "create_text_channel"])
        self.assertEqual(report.id_map, {10: 901, 20: 902})
        self.assertIn(new_role, self.channel_kwargs["overwrites"])
        self.assertEqual(report.role_members, [(new_role, [5, 6])])
        guild.edit_role_positions.assert_awaited_once()
        self.assertEqual(report.failed, [])

    async def test_ban_is_undone_and_existing_restore_is_skipped(self):
        guild = SimpleNamespace(
            unban=AsyncMock(),
            get_role=lambda role_id: FakeRole(role_id, 1) if role_id == 901 else None,
            create_role=AsyncMock(),
            me=SimpleNamespace(top_role=FakeRole(999, 50)),
        )
        report = RevertReport({10: 901})
        cog = AntiNuke(SimpleNamespace(db=make_db()))
        await cog.revert_entries(guild, [
            {"action": "ban", "target_id": 77},
            {"action": "role_delete", "target_id": 10, "data": {"name": "Mods"}},
        ], report)
        guild.unban.assert_awaited_once()
        guild.create_role.assert_not_awaited()


class LockdownTests(GuildTestCase):
    def make_guild(self, channels):
        everyone = FakeRole(GUILD_ID, 0, default=True)
        return SimpleNamespace(
            id=GUILD_ID,
            default_role=everyone,
            text_channels=channels,
            verification_level=discord.VerificationLevel.low,
            edit=AsyncMock(),
            get_channel=lambda channel_id: next((c for c in channels if c.id == channel_id), None),
        ), everyone

    def make_channel(self, channel_id, overwrite, can_send=True):
        channel = SimpleNamespace(id=channel_id, set_permissions=AsyncMock(), overwrites={})
        channel.permissions_for = lambda role: SimpleNamespace(send_messages=can_send)
        return channel, overwrite

    @patch("cogs.security.antiraid.send_security_alert", new_callable=AsyncMock)
    async def test_lock_then_unlock_restores_exact_overwrites(self, _alert):
        original = discord.PermissionOverwrite(view_channel=True, attach_files=False)
        with_overwrite, _ = self.make_channel(1, original)
        without_overwrite, _ = self.make_channel(2, None)
        read_only, _ = self.make_channel(3, None, can_send=False)
        guild, everyone = self.make_guild([with_overwrite, without_overwrite, read_only])
        with_overwrite.overwrites = {everyone: original}

        db = make_db()
        cog = AntiRaid(SimpleNamespace(db=db))
        locked = await cog.lock(guild, manual=False, reason="test")

        self.assertEqual(locked, 2)
        read_only.set_permissions.assert_not_awaited()
        lock_ow = with_overwrite.set_permissions.await_args.kwargs["overwrite"]
        self.assertFalse(lock_ow.send_messages)
        self.assertTrue(lock_ow.view_channel)
        saved = db.save_lockdown.await_args.args[1]
        self.assertEqual(saved["channels"]["2"], None)
        self.assertEqual(saved["verification_level"], discord.VerificationLevel.low.value)

        db.get_lockdown.return_value = saved
        await cog.unlock(guild)

        restored = with_overwrite.set_permissions.await_args.kwargs["overwrite"]
        self.assertEqual(restored.pair(), original.pair())
        self.assertIsNone(without_overwrite.set_permissions.await_args.kwargs["overwrite"])
        self.assertEqual(guild.edit.await_args.kwargs["verification_level"], discord.VerificationLevel.low)
        db.clear_lockdown.assert_awaited_once()


class JoinAndSpamTests(GuildTestCase):
    def make_member(self, age_days, member_id=50):
        return SimpleNamespace(
            id=member_id, bot=False,
            guild=SimpleNamespace(id=GUILD_ID, name="ATL"),
            created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
            send=AsyncMock(), kick=AsyncMock(),
        )

    async def test_young_account_is_kicked(self):
        member = self.make_member(1)
        await AntiRaid(SimpleNamespace(db=make_db())).on_member_join(member)
        member.kick.assert_awaited_once()

    async def test_older_account_is_allowed(self):
        member = self.make_member(4)
        await AntiRaid(SimpleNamespace(db=make_db())).on_member_join(member)
        member.kick.assert_not_awaited()

    async def test_join_spike_locks_and_kicks_young_joiners(self):
        cog = AntiRaid(SimpleNamespace(db=make_db()))
        cog.lock = AsyncMock()
        members = [self.make_member(10, member_id=i) for i in range(10)]
        members[0].created_at = datetime.now(timezone.utc) - timedelta(days=400)
        lookup = {m.id: m for m in members}
        for m in members:
            m.guild = SimpleNamespace(id=GUILD_ID, name="ATL", get_member=lookup.get)
            await cog.on_member_join(m)
        cog.lock.assert_awaited_once()
        members[0].kick.assert_not_awaited()
        self.assertTrue(all(m.kick.await_count == 1 for m in members[1:]))

    async def test_everyone_ping_from_member_is_deleted_without_timeout(self):
        author = MagicMock(spec=discord.Member)
        author.bot = False
        author.id = 50
        author.guild = SimpleNamespace(owner_id=OWNER_ID)
        author.guild_permissions = discord.Permissions.none()
        author.roles = []
        author.timeout = AsyncMock()
        message = SimpleNamespace(
            author=author, guild=SimpleNamespace(id=GUILD_ID), content="@everyone free robux",
            delete=AsyncMock(),
        )
        await AntiRaid(SimpleNamespace(db=make_db())).on_message(message)
        message.delete.assert_awaited_once()
        author.timeout.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
