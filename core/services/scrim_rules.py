"""Pure scrim rules. The Roblox script mirrors these; keep both in sync (see tests/test_scrim_rules.py vectors)."""
import random
from typing import List, Optional, Sequence, Tuple

POINTS_TO_WIN = 7
WIN_BY = 2
POINT_CAP = 10
MAX_PER_TEAM = 5


def is_match_over(a: int, b: int) -> bool:
    """First to 7 and ahead by 2, hard cap at 10."""
    hi, lo = max(a, b), min(a, b)
    if hi >= POINT_CAP:
        return True
    return hi >= POINTS_TO_WIN and hi - lo >= WIN_BY


def match_winner(a: int, b: int) -> Optional[int]:
    """0 if side A won, 1 if side B won, None while the match is live."""
    if not is_match_over(a, b) or a == b:
        return None
    return 0 if a > b else 1


def team_size(player_count: int, min_players: int) -> int:
    """Players per side for an even split, or 0 if the scrim cannot start yet."""
    per_side = min(player_count // 2, MAX_PER_TEAM)
    if per_side < 1 or per_side * 2 < min_players:
        return 0
    return per_side


def pick_teams(
    user_ids: Sequence[int], per_side: int, rng: Optional[random.Random] = None
) -> Tuple[List[int], List[int], List[int]]:
    """Randomly choose (side_a, side_b, surplus). Surplus are the players who do not get a slot."""
    rng = rng or random.Random()
    pool = list(user_ids)
    rng.shuffle(pool)
    side_a = pool[:per_side]
    side_b = pool[per_side:per_side * 2]
    surplus = pool[per_side * 2:]
    return side_a, side_b, surplus


def draft_next_turn(black_picks: int, white_picks: int, per_side: int, last_team: Optional[str] = None) -> Optional[str]:
    """Who picks next. Strict alternation starting with Black (captain 1); None when both sides are full.

    Captains already hold a slot, so each side needs per_side - 1 picks. If one side is full the other keeps picking.
    """
    needed = max(per_side - 1, 0)
    counts = {"Black": black_picks, "White": white_picks}
    preferred = "White" if last_team == "Black" else "Black"
    other = "Black" if preferred == "White" else "White"
    if counts[preferred] < needed:
        return preferred
    if counts[other] < needed:
        return other
    return None
