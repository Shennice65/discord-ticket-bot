import random

import pytest

from core.services.scrim_rules import is_match_over, match_winner, pick_teams, team_size


@pytest.mark.parametrize(
    "a,b,over",
    [
        (0, 0, False),
        (6, 0, False),
        (7, 0, True),
        (7, 5, True),
        (7, 6, False),
        (8, 6, True),
        (8, 7, False),
        (9, 7, True),
        (9, 8, False),
        (9, 9, False),
        (10, 9, True),
        (9, 10, True),
        (10, 8, True),
    ],
)
def test_is_match_over(a, b, over):
    assert is_match_over(a, b) is over
    assert is_match_over(b, a) is over


def test_match_winner():
    assert match_winner(7, 3) == 0
    assert match_winner(3, 7) == 1
    assert match_winner(10, 9) == 0
    assert match_winner(7, 6) is None


@pytest.mark.parametrize(
    "players,min_players,size",
    [(0, 6, 0), (5, 6, 0), (6, 6, 3), (7, 6, 3), (9, 6, 4), (10, 6, 5), (14, 6, 5), (4, 4, 2), (3, 4, 0)],
)
def test_team_size(players, min_players, size):
    assert team_size(players, min_players) == size


def test_pick_teams_partitions_without_overlap():
    ids = list(range(1, 15))
    a, b, surplus = pick_teams(ids, 5, random.Random(1))
    assert len(a) == len(b) == 5
    assert len(surplus) == 4
    assert sorted(a + b + surplus) == ids


def test_pick_teams_is_not_input_ordered():
    ids = list(range(1, 11))
    picks = {tuple(pick_teams(ids, 5, random.Random(seed))[0]) for seed in range(20)}
    assert len(picks) > 1


from core.services.scrim_rules import draft_next_turn


def test_draft_alternates_black_first_until_full():
    turns, black, white, last = [], 0, 0, None
    while (nxt := draft_next_turn(black, white, 5, last)) is not None:
        turns.append(nxt)
        black, white, last = black + (nxt == "Black"), white + (nxt == "White"), nxt
    assert turns == ["Black", "White"] * 4


def test_draft_one_side_full_other_keeps_picking():
    assert draft_next_turn(2, 0, 3, "Black") == "White"
    assert draft_next_turn(2, 1, 3, "White") == "White"
    assert draft_next_turn(2, 2, 3, "White") is None


def test_draft_has_no_picks_for_one_v_one():
    assert draft_next_turn(0, 0, 1) is None
