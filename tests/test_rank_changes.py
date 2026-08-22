import unittest

from database.ladder import calculate_movement_metadata, calculate_rank_changes, is_unranked_rank
from utils.ladder_utils import TIERS


class RankChangeCalculationTests(unittest.TestCase):
    def test_empty_and_explicit_unranked_ranks_are_new(self):
        self.assertTrue(is_unranked_rank(None))
        self.assertTrue(is_unranked_rank(""))
        self.assertTrue(is_unranked_rank("Unranked"))
        self.assertFalse(is_unranked_rank("Novice 3"))

    def test_calculates_real_global_position_delta(self):
        current = [
            {"user_id": 1, "rank": "Phantoms 1"},
            {"user_id": 2, "rank": "Champions 1"},
            {"user_id": 3, "rank": "Champions 2"},
            {"user_id": 4, "rank": "Elites 1"},
        ]
        tiers = {tier: [] for tier in TIERS}
        tiers["Phantoms"] = [1]
        tiers["Champions"] = [3, 2]
        tiers["Elites"] = [4]

        self.assertEqual(
            {1: 0, 3: 1, 2: -1, 4: 0},
            calculate_rank_changes(current, tiers, TIERS),
        )

    def test_new_player_starts_with_no_movement(self):
        current = [{"user_id": 1, "rank": "Champions 1"}]
        tiers = {tier: [] for tier in TIERS}
        tiers["Champions"] = [1, 2]

        self.assertEqual(
            {1: 0, 2: 0},
            calculate_rank_changes(current, tiers, TIERS),
        )

    def test_movement_window_keeps_latest_event_from_each_source(self):
        player = {
            "rank_change_events": [
                {"version": 9, "delta": 20, "role": "passive", "source": "ranked"},
                {"version": 10, "delta": 9, "role": "passive", "source": "admin"},
                {"version": 11, "delta": 3, "role": "direct", "source": "ranked"},
            ]
        }

        history, delta, role = calculate_movement_metadata(
            player,
            event_version=12,
            event_delta=-1,
            event_role="passive",
            movement_source="observation",
        )

        self.assertEqual([10, 11, 12], [event["version"] for event in history])
        self.assertEqual(["admin_manual", "ranked", "observation"], [event["source"] for event in history])
        self.assertEqual(11, delta)
        self.assertEqual("direct", role)

    def test_new_event_replaces_only_its_own_source(self):
        player = {
            "rank_change_events": [
                {"version": 10, "delta": 2, "role": "direct", "source": "admin_manual"},
                {"version": 11, "delta": 3, "role": "direct", "source": "ranked"},
                {"version": 12, "delta": 4, "role": "passive", "source": "observation"},
            ]
        }

        history, delta, role = calculate_movement_metadata(
            player,
            event_version=13,
            event_delta=5,
            event_role="passive",
            movement_source="ranked",
        )

        self.assertEqual([10, 12, 13], [event["version"] for event in history])
        self.assertEqual(11, delta)
        self.assertEqual("direct", role)


if __name__ == "__main__":
    unittest.main()
