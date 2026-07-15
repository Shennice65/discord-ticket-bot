import re

TIERS = ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]

def parse_rank(rank_str: str):
    match = re.match(r'^([a-zA-Z]+)\s*(\d+)$', rank_str)
    if not match: return None
    tier = match.group(1).capitalize()
    num = int(match.group(2))
    return (tier, num)

def get_sort_key(rank_str: str):
    parsed = parse_rank(rank_str)
    if not parsed:
        return (99, 999999)
    try:
        tier_idx = TIERS.index(parsed[0])
    except ValueError:
        tier_idx = 99
    return (tier_idx, parsed[1])
