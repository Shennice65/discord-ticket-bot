"""Backfill rank_change from recent ladder-changing events.

Dry-run by default:
    python scripts/backfill_rank_changes.py

Apply after reviewing the preview:
    python scripts/backfill_rank_changes.py --apply
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument, UpdateOne

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.ladder import calculate_rank_changes
from utils.ladder_utils import TIERS, parse_rank


def build_tier_lists(players: list[dict]) -> dict[str, list[int]]:
    tiers = {tier: [] for tier in TIERS}
    for player in players:
        parsed = parse_rank(player.get("rank", ""))
        if parsed and parsed[0] in tiers:
            tiers[parsed[0]].append((player["user_id"], parsed[1]))
    for tier in TIERS:
        tiers[tier].sort(key=lambda item: item[1])
        tiers[tier] = [user_id for user_id, _ in tiers[tier]]
    return tiers


def reconstruct_previous_ladder(
    current_tiers: dict[str, list[int]],
    changes: list[dict],
) -> dict[str, list[int]]:
    previous = {tier: list(user_ids) for tier, user_ids in current_tiers.items()}
    target_ids = {change["target_id"] for change in changes}
    for user_ids in previous.values():
        user_ids[:] = [user_id for user_id in user_ids if user_id not in target_ids]

    insertions = []
    for change in changes:
        old_rank = change.get("old_rank") or ""
        if not old_rank or old_rank.lower() == "unranked":
            continue
        parsed_old = parse_rank(old_rank)
        if not parsed_old or parsed_old[0] not in previous:
            raise ValueError(f"Invalid stored old rank: {old_rank!r}")
        insertions.append((TIERS.index(parsed_old[0]), parsed_old[1], change["target_id"]))

    for tier_index, old_number, target_id in sorted(insertions):
        tier = TIERS[tier_index]
        previous[tier].insert(max(0, old_number - 1), target_id)
    return previous


def place_at_rank(
    tiers: dict[str, list[int]],
    user_id: int,
    rank: str | None,
) -> dict[str, list[int]]:
    """Repair an incomplete historical snapshot without touching the database."""
    repaired = {tier: list(user_ids) for tier, user_ids in tiers.items()}
    for user_ids in repaired.values():
        if user_id in user_ids:
            user_ids.remove(user_id)
    if not rank:
        return repaired
    parsed = parse_rank(rank)
    if not parsed or parsed[0] not in repaired:
        raise ValueError(f"Invalid historical rank: {rank!r}")
    tier, number = parsed
    repaired[tier].insert(max(0, number - 1), user_id)
    return repaired


def group_recent_events(logs: list[dict], limit: int) -> list[list[dict]]:
    """Group the two undo rows produced by one match into one ladder event."""
    ordered = sorted(logs, key=lambda log: str(log.get("timestamp", "")), reverse=True)
    events: list[list[dict]] = []
    index = 0
    while index < len(ordered) and len(events) < limit:
        log = ordered[index]
        event = [log]
        action = log.get("action_type")
        if action in {"match_winner", "match_loser"} and index + 1 < len(ordered):
            other = ordered[index + 1]
            other_action = other.get("action_type")
            if {action, other_action} == {"match_winner", "match_loser"}:
                event.append(other)
                index += 1
        events.append(event)
        index += 1
    return events


def latest_ladder_events(database, limit: int) -> list[list[dict]]:
    """Return enough complete history to cross intervening ignored mutations."""
    history_limit = max(100, limit * 25)
    logs = list(database.undo_logs.find({}).sort("timestamp", -1).limit(history_limit * 2))
    return group_recent_events(logs, history_limit)


def is_display_event(event: list[dict]) -> bool:
    """Only ranked matches and observation/admin placements drive UI movement."""
    actions = {change.get("action_type") for change in event}
    return bool(actions & {"match_winner", "match_loser", "force_set_rank"})


def movement_source_for_event(event: list[dict]) -> str:
    stored_source = next((change.get("source") for change in event if change.get("source")), None)
    if stored_source in {"ranked", "observation", "admin_manual"}:
        return stored_source
    if any(str(change.get("action_type", "")).startswith("match_") for change in event):
        return "ranked"
    # Old force-set logs predate source tracking and cannot distinguish an
    # observation from an admin command. Admin is the conservative fallback.
    return "admin_manual"


def latest_ranked_matches(database, limit: int) -> list[dict]:
    pipeline = [
        {"$match": {"status": "closed", "ticket_type": "Ranked 1v1"}},
        {"$sort": {"closed_at": -1}},
        {"$lookup": {
            "from": "ranked_results",
            "localField": "id",
            "foreignField": "ticket_id",
            "as": "result",
        }},
        {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}},
        {"$limit": limit},
    ]
    return list(database.tickets.aggregate(pipeline))


def rank_at(tiers: dict[str, list[int]], user_id: int) -> str | None:
    for tier in TIERS:
        if user_id in tiers[tier]:
            return f"{tier} {tiers[tier].index(user_id) + 1}"
    return None


def players_from_tiers(tiers: dict[str, list[int]]) -> list[dict]:
    return [
        {"user_id": user_id, "rank": f"{tier} {index + 1}"}
        for tier in TIERS
        for index, user_id in enumerate(tiers[tier])
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write calculated values to player_ranks")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--events", type=int, default=2, help="Number of latest ladder events to aggregate (default: 2)")
    mode.add_argument("--matches", type=int, help="Legacy mode: aggregate only Ranked 1v1 tickets")
    args = parser.parse_args()
    count = args.matches if args.matches is not None else args.events
    if count < 1 or count > 50:
        parser.error("event count must be between 1 and 50")

    load_dotenv(ROOT / ".env")
    mongo_uri = os.environ.get("MONGO_URI")
    if not mongo_uri:
        print("MONGO_URI is not configured.")
        return 1

    client = MongoClient(
        mongo_uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=10_000,
    )
    database = client.discord_bot_db
    database.command("ping")

    players = list(database.player_ranks.find({}, {"user_id": 1, "username": 1, "rank": 1}))
    players_by_id = {player["user_id"]: player for player in players}
    current_tiers = build_tier_lists(players)
    changes = {player["user_id"]: 0 for player in players}
    event_records = []
    processed = 0

    if args.matches is not None:
        matches = latest_ranked_matches(database, args.matches)
        events = []
        for match in matches:
            result = match["result"]
            winner_id = result.get("winner_id")
            loser_id = match.get("opponent_id") if winner_id == match.get("user_id") else match.get("user_id")
            events.append([
                {"target_id": winner_id, "action_type": "match_winner", "old_rank": result.get("winner_old") or result.get("starting_rank"), "new_rank": result.get("winner_new") or result.get("ending_rank")},
                {"target_id": loser_id, "action_type": "match_loser", "old_rank": result.get("loser_old") or rank_at(current_tiers, loser_id), "new_rank": result.get("loser_new")},
            ])
    else:
        events = latest_ladder_events(database, args.events)

    if not events:
        print("No ladder-changing history was found.")
        return 1

    for event in events:
        if processed >= count:
            break
        for change in event:
            expected = change.get("new_rank") or None
            if isinstance(expected, str) and expected.lower() == "unranked":
                expected = None
            actual = rank_at(current_tiers, change["target_id"])
            if actual != expected:
                print(
                    "Historical gap repaired in memory | "
                    f"player {change['target_id']}: expected {expected!r}, reconstructed {actual!r}"
                )
                current_tiers = place_at_rank(current_tiers, change["target_id"], expected)

        previous_tiers = reconstruct_previous_ladder(current_tiers, event)
        summary = ", ".join(
            f"{item.get('action_type')} {item['target_id']}: {item.get('old_rank') or 'Unranked'} -> {item.get('new_rank') or 'Unranked'}"
            for item in event
        )
        if args.matches is not None or is_display_event(event):
            event_changes = calculate_rank_changes(players_from_tiers(previous_tiers), current_tiers, TIERS)
            direct_ids = {item["target_id"] for item in event}
            current_ranked_ids = {
                user_id for user_id, player in players_by_id.items() if parse_rank(player.get("rank", ""))
            }
            has_visible_direct_promotion = any(
                event_changes.get(user_id, 0) > 0 and user_id in current_ranked_ids
                for user_id in direct_ids
            )
            if args.matches is not None or has_visible_direct_promotion:
                for user_id, delta in event_changes.items():
                    changes[user_id] = changes.get(user_id, 0) + delta
                event_records.append((event, event_changes))
                processed += 1
                print(f"Event {processed} | {summary}")
            else:
                print(f"Reconstructed event without a visible direct promotion | {summary}")
        else:
            print(f"Reconstructed ignored event | {summary}")
        current_tiers = previous_tiers

    if processed != count:
        print(f"Only {processed} matching events were available.")

    moved = [(user_id, delta) for user_id, delta in changes.items() if delta]
    moved.sort(key=lambda item: (-item[1], item[0]))
    print(f"Aggregated latest ladder events: {processed}")
    print(f"Players with movement: {len(moved)}")
    for user_id, delta in moved:
        player = players_by_id.get(user_id, {})
        name = player.get("username") or str(user_id)
        arrow = "UP" if delta > 0 else "DOWN"
        print(f"  {name} ({user_id}): {arrow} {abs(delta)}")

    if not args.apply:
        print("Dry run only. Re-run with --apply to save these values.")
        return 0

    version_doc = database.bot_settings.find_one_and_update(
        {"key": "ladder_event_version"},
        {"$inc": {"value": processed}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    newest_version = version_doc["value"]
    oldest_version = newest_version - processed + 1
    chronological_events = list(reversed(event_records))

    operations = [
        UpdateOne(
            {"user_id": user_id},
            {"$set": {
                "rank_change": delta,
                "rank_change_events": [
                    {
                        "version": oldest_version + event_index,
                        "delta": event_changes.get(user_id, 0),
                        "role": "direct" if user_id in {item["target_id"] for item in event} else (
                            "passive" if event_changes.get(user_id, 0) else "none"
                        ),
                        "source": movement_source_for_event(event),
                    }
                    for event_index, (event, event_changes) in enumerate(chronological_events)
                ],
                "movement_role": "direct" if any(
                    user_id in {item["target_id"] for item in event}
                    for event, _ in chronological_events
                ) else ("passive" if delta else "none"),
                "movement_source": next((
                    movement_source_for_event(event)
                    for event, _ in reversed(chronological_events)
                    if user_id in {item["target_id"] for item in event}
                ), "admin_manual"),
                "movement_event_version": newest_version,
            }},
        )
        for user_id, delta in changes.items()
    ]
    if operations:
        database.player_ranks.bulk_write(operations, ordered=False)
    print(f"Applied rank_change values; {len(moved)} players show movement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
