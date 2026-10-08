import unittest
from datetime import timedelta
from types import SimpleNamespace

import discord

from utils.security.journal import ActionJournal
from utils.security.snapshot import order_for_restore, remap_overwrites, serialize_channel
from utils.security.spam import (
    LOCK_PERMISSIONS,
    JoinSpikeDetector,
    SpamDetector,
    StrikeLadder,
    locked_pair,
    mentions_everyone,
)
from utils.security.tracker import ActionTracker, classify

ADMIN = discord.Permissions(administrator=True).value
SEND = discord.Permissions(send_messages=True).value
VIEW = discord.Permissions(view_channel=True).value


class TrackerTests(unittest.TestCase):
    def test_third_channel_delete_in_window_triggers(self):
        tracker = ActionTracker()
        self.assertFalse(tracker.record(1, "channel_delete", now=0))
        self.assertFalse(tracker.record(1, "channel_delete", now=5))
        self.assertTrue(tracker.record(1, "channel_delete", now=10))

    def test_actions_outside_window_do_not_count(self):
        tracker = ActionTracker()
        tracker.record(1, "channel_delete", now=0)
        tracker.record(1, "channel_delete", now=1)
        self.assertFalse(tracker.record(1, "channel_delete", now=20))

    def test_actors_are_counted_separately_and_reset(self):
        tracker = ActionTracker()
        tracker.record(1, "ban_kick", now=0)
        tracker.record(2, "ban_kick", now=0)
        self.assertFalse(tracker.record(1, "ban_kick", now=1))
        tracker.reset(1)
        self.assertFalse(tracker.record(1, "ban_kick", now=2))

    def test_webhook_create_triggers_on_second(self):
        tracker = ActionTracker()
        self.assertFalse(tracker.record(1, "webhook_create", now=0))
        self.assertTrue(tracker.record(1, "webhook_create", now=1))


class ClassifyTests(unittest.TestCase):
    def test_granting_admin_to_role_is_instant(self):
        self.assertEqual(classify("role_update", before_perms=0, after_perms=ADMIN), ("mass_edit", True))

    def test_removing_permissions_is_not_instant(self):
        self.assertEqual(classify("role_update", before_perms=ADMIN, after_perms=0), ("mass_edit", False))

    def test_creating_admin_role_is_instant(self):
        self.assertTrue(classify("role_create", before_perms=0, after_perms=ADMIN)[1])

    def test_everyone_overwrite_with_dangerous_perm_is_instant(self):
        manage = discord.Permissions(manage_channels=True).value
        self.assertTrue(classify("overwrite_update", before_perms=0, after_perms=manage, target_is_everyone=True)[1])
        self.assertFalse(classify("overwrite_update", before_perms=0, after_perms=manage, target_is_everyone=False)[1])

    def test_giving_member_admin_role_is_instant(self):
        self.assertTrue(classify("member_role_update", added_role_perms=[ADMIN])[1])
        self.assertFalse(classify("member_role_update", added_role_perms=[SEND])[1])

    def test_bot_add_prune_and_vanity_are_instant(self):
        self.assertTrue(classify("bot_add")[1])
        self.assertTrue(classify("member_prune")[1])
        self.assertTrue(classify("guild_update", vanity_changed=True)[1])

    def test_channel_delete_uses_threshold(self):
        self.assertEqual(classify("channel_delete"), ("channel_delete", False))


class JournalTests(unittest.TestCase):
    def test_take_returns_recent_entries_in_order_and_clears(self):
        journal = ActionJournal(keep_seconds=60)
        journal.add(1, {"n": 1}, now=0)
        journal.add(1, {"n": 2}, now=50)
        journal.add(1, {"n": 3}, now=100)
        self.assertEqual(journal.take(1), [{"n": 2}, {"n": 3}])
        self.assertEqual(journal.take(1), [])


class SnapshotTests(unittest.TestCase):
    def test_restore_order_roles_categories_channels_then_rest(self):
        entries = [
            {"action": "ban", "target_id": 1},
            {"action": "channel_delete", "target_id": 2, "data": {"type": 0, "position": 1}},
            {"action": "guild_update", "target_id": 3},
            {"action": "channel_delete", "target_id": 4, "data": {"type": 4, "position": 5}},
            {"action": "role_delete", "target_id": 5, "data": {"position": 3}},
        ]
        self.assertEqual([e["target_id"] for e in order_for_restore(entries)], [5, 4, 2, 1, 3])

    def test_remap_overwrites_points_at_new_roles(self):
        overwrites = [{"id": 10, "type": 0, "allow": 1, "deny": 0}, {"id": 11, "type": 1, "allow": 0, "deny": 1}]
        self.assertEqual([o["id"] for o in remap_overwrites(overwrites, {10: 99})], [99, 11])

    def test_serialize_channel_keeps_overwrites_and_children(self):
        child = SimpleNamespace(id=7)
        channel = SimpleNamespace(
            id=5, type=discord.ChannelType.category, name="Info", position=2, category_id=None,
            _overwrites=[SimpleNamespace(id=1, type=0, allow=VIEW, deny=SEND)], channels=[child],
        )
        data = serialize_channel(channel)
        self.assertEqual(data["type"], 4)
        self.assertEqual(data["overwrites"], [{"id": 1, "type": 0, "allow": VIEW, "deny": SEND}])
        self.assertEqual(data["children"], [7])


class SpamTests(unittest.TestCase):
    def test_flood_triggers_on_tenth_message_in_seven_seconds(self):
        spam = SpamDetector()
        for i in range(9):
            self.assertEqual(spam.check(1, 100, i, f"msg {i}", 0, now=i * 0.5)[0], None)
        rule, to_delete = spam.check(1, 100, 9, "msg 9", 0, now=4.6)
        self.assertEqual(rule, "message_flood")
        self.assertEqual(len(to_delete), 10)

    def test_slow_chatting_is_fine(self):
        spam = SpamDetector()
        for i in range(30):
            self.assertIsNone(spam.check(1, 100, i, f"msg {i}", 0, now=i * 1.0)[0])

    def test_mass_mentions(self):
        spam = SpamDetector()
        self.assertIsNone(spam.check(1, 100, 1, "hi", 7, now=0)[0])
        self.assertEqual(spam.check(1, 100, 2, "hi", 8, now=1)[0], "mass_mentions")

    def test_invite_spam(self):
        spam = SpamDetector()
        for i in range(4):
            self.assertIsNone(spam.check(1, 100, i, f"join discord.gg/abc{i}", 0, now=i * 10)[0])
        self.assertEqual(spam.check(1, 100, 4, "discord.gg/zzz", 0, now=45)[0], "invite_spam")

    def test_same_message_in_four_channels(self):
        spam = SpamDetector()
        for channel in range(3):
            self.assertIsNone(spam.check(1, channel, channel, "FREE NITRO", 0, now=channel * 2)[0])
        self.assertEqual(spam.check(1, 3, 3, "free   nitro", 0, now=8)[0], "cross_channel_duplicate")

    def test_strike_ladder_warns_then_times_out_and_resets(self):
        ladder = StrikeLadder()
        self.assertEqual(ladder.add(1, now=0), (1, None))
        self.assertEqual(ladder.add(1, now=60), (2, timedelta(minutes=5)))
        self.assertEqual(ladder.add(1, now=120), (3, timedelta(minutes=30)))
        self.assertEqual(ladder.add(1, now=180), (4, timedelta(minutes=30)))
        self.assertEqual(ladder.add(1, now=180 + 31 * 60), (1, None))

    def test_everyone_detection(self):
        self.assertTrue(mentions_everyone("hey @everyone"))
        self.assertTrue(mentions_everyone("@here look"))
        self.assertFalse(mentions_everyone("everyone hi"))


class RaidTests(unittest.TestCase):
    def test_join_spike(self):
        spikes = JoinSpikeDetector()
        for i in range(9):
            self.assertFalse(spikes.record(i, now=i))
        self.assertTrue(spikes.record(9, now=9))
        self.assertEqual(len(spikes.recent_ids()), 10)

    def test_joins_spread_out_are_not_a_spike(self):
        spikes = JoinSpikeDetector()
        self.assertFalse(any(spikes.record(i, now=i * 5) for i in range(20)))

    def test_locked_pair_only_touches_send_permissions(self):
        allow, deny = locked_pair(VIEW | SEND, 0)
        self.assertEqual(allow, VIEW)
        self.assertEqual(deny, LOCK_PERMISSIONS)


if __name__ == "__main__":
    unittest.main()
