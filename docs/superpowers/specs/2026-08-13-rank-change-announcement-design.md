# Rank Change Announcements in Tickets Design

## Problem
When a player's rank changes (e.g. from an Admin command or a Personal Observation ticket closing), they might have an open Ranked 1v1 ticket. We need to announce their new rank in their open ticket(s) and explicitly warn them if the match is now outside the 5-rank range, so they can decide whether to proceed or cancel.

## Architecture & Implementation
- Add a new method `check_and_notify_rank_change(user_id: int, new_rank: str)` to `TicketService`.
- This method will:
  1. Query the database for any open `"Ranked 1v1"` tickets where `user_id` is either the requester (`user_id`) or the opponent (`opponent_id`).
  2. For each open ticket found:
     - Fetch the current rank index of both players using `db.get_global_rank_index`.
     - Calculate the rank difference.
     - Send a message in the ticket's channel announcing the new rank.
     - If the difference is > 5, explicitly warn them that the match is out of range.
- Call this method from:
  1. `cogs/ranking_admin.py` in `/setrank` command after a successful rank set.
  2. `cogs/tickets.py` in the Personal Observation closing flow, after `force_set_player_rank`.
  3. `cogs/tickets.py` in the Ranked 1v1 closing flow, after `process_match_result` (checking both winner and loser, just in case they have another ticket open).
  4. Any other place where rank is updated (e.g. `refactor.py` or scripts, though those are batch). We'll focus on the live runtime commands.

## Requirements
- Announce the new rank clearly in the ticket.
- Explicitly state if the match is out of range.
- Do not automatically close the ticket, leave it up to the players/observers.
