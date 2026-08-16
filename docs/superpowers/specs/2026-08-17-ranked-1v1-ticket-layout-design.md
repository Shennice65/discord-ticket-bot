# Ranked 1v1 Ticket Layout Redesign

## Summary
Redesign the Ranked 1v1 ticket embed layout to match a streamlined competitive lobby format. The embed includes an author header for the host/challenger, a 3-column monospace table showing Player names, Ranks, and Winrates, a dynamic rank tier lobby badge thumbnail loaded from `assets/tiers/`, and a clean instructions footer.

## Embed Layout Specification

### Visual Elements
1. **Color**: Sleek dark theme (`0x2b2d31`).
2. **Author**: Challenger display name and avatar (`embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)`).
3. **Table Grid (Monospace Codeblock in Description)**:
   ```text
   │ [Player]         │ [Rank]          │ [Winrate] │
   │ <Challenger>     │ <Rank>          │ <Rate>%   │
   │ <Opponent>       │ <Rank>          │ <Rate>%   │
   ```
   - Dynamic padding ensures columns align cleanly across all devices (desktop and mobile).
   - Names truncated if over 16 characters to preserve grid alignment.
4. **Thumbnail**:
   - Resolved from Challenger's rank tier (e.g. `Champions` -> `assets/tiers/champions.png`).
   - Attached via `discord.File` and referenced via `attachment://tier.png`.
   - Safe fallback if tier icon is missing or player is unranked.
5. **Footer**:
   - `Wait for an observer to referee your match before starting.`

## Technical Architecture & Changes

### 1. `utils/embeds.py`
- Add helper method `create_ranked_1v1_ticket_embed(user: discord.Member, opponent_name: str, u_rank: str, o_rank: str, u_rate: float, o_rate: float) -> tuple[discord.Embed, Optional[discord.File]]`.
- Format table using padded columns:
  - Header: `│ [Player]        │ [Rank]          │ [Winrate] │`
  - Rows for Challenger and Opponent.
- Resolve tier badge from `assets/tiers/{tier.lower()}.png` using `parse_rank` from `utils.ranking_utils`.
- Preserve backward compatibility for `TicketEmbeds.ticket_created` (used for Personal Observation tickets).

### 2. `cogs/tickets/core.py`
- In `create_ranked_ticket` and `_finalize_out_of_range_ticket`:
  - Build embed and tier attachment using the new helper.
  - Send message with `embed=embed` and `file=tier_file` (if `tier_file` is not None).

## Verification Plan
1. **Automated / Unit Testing**:
   - Test `create_ranked_1v1_ticket_embed` with various rank combinations (`Champions 1`, `Unranked`, `Novice 5`, long usernames).
   - Verify table padding and alignment.
   - Verify tier image file generation and fallback when asset is missing.
2. **Bot Syntax & Lint Verification**:
   - Run python compile check on modified files to verify zero errors.
