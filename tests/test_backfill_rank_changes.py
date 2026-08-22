import unittest

from scripts.backfill_rank_changes import build_tier_lists, group_recent_events, is_display_event, movement_source_for_event, place_at_rank, reconstruct_previous_ladder


class RankChangeBackfillTests(unittest.TestCase):
    def test_reconstructs_winner_previous_position(self):
        current = {
            "Phantoms": [],
            "Champions": [3, 2],
            "Elites": [4, 1],
            "Legends": [],
            "Masters": [],
            "Novice": [],
        }

        previous = reconstruct_previous_ladder(current, [{"target_id": 3, "old_rank": "Elites 2"}])

        self.assertEqual([2], previous["Champions"])
        self.assertEqual([4, 3, 1], previous["Elites"])

    def test_builds_ordered_tier_lists(self):
        players = [
            {"user_id": 2, "rank": "Champions 2"},
            {"user_id": 1, "rank": "Champions 1"},
            {"user_id": 9, "rank": ""},
        ]

        self.assertEqual([1, 2], build_tier_lists(players)["Champions"])

    def test_reconstructs_multi_player_match(self):
        current = {tier: [] for tier in ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]}
        current["Legends"] = [1, 2, 3, 4]

        previous = reconstruct_previous_ladder(current, [
            {"target_id": 3, "old_rank": "Legends 4"},
            {"target_id": 4, "old_rank": "Legends 3"},
        ])

        self.assertEqual([1, 2, 4, 3], previous["Legends"])

    def test_groups_match_pair_as_one_event(self):
        logs = [
            {"target_id": 2, "action_type": "match_loser", "timestamp": "2026-01-01T00:00:02"},
            {"target_id": 1, "action_type": "match_winner", "timestamp": "2026-01-01T00:00:01"},
            {"target_id": 3, "action_type": "force_set_rank", "timestamp": "2025-12-31T23:59:00"},
        ]

        events = group_recent_events(logs, 2)

        self.assertEqual(2, len(events))
        self.assertEqual(2, len(events[0]))
        self.assertEqual("force_set_rank", events[1][0]["action_type"])

    def test_only_supported_sources_count_as_display_events(self):
        self.assertTrue(is_display_event([{"action_type": "match_winner"}]))
        self.assertTrue(is_display_event([{"action_type": "force_set_rank"}]))
        self.assertFalse(is_display_event([{"action_type": "remove_player"}]))
        self.assertFalse(is_display_event([{"action_type": "self_unrank"}]))

    def test_repairs_missing_player_in_historical_snapshot(self):
        tiers = {tier: [] for tier in ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]}
        tiers["Masters"] = [1, 2]

        repaired = place_at_rank(tiers, 9, "Masters 2")

        self.assertEqual([1, 9, 2], repaired["Masters"])
        self.assertEqual([], tiers["Legends"])

    def test_resolves_movement_badge_source(self):
        self.assertEqual("ranked", movement_source_for_event([{"action_type": "match_winner"}]))
        self.assertEqual("observation", movement_source_for_event([
            {"action_type": "force_set_rank", "source": "observation"}
        ]))
        self.assertEqual("admin_manual", movement_source_for_event([{"action_type": "force_set_rank"}]))


if __name__ == "__main__":
    unittest.main()
