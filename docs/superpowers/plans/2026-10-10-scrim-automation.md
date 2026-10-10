# Scrim Automation Plan (Discord bot + Roblox host script)

**Goal:** `/scrim start <private-server-link>` runs a hands-off loop: lobby → 30s reset-countdown → verify/kick → random 5v5 (or even smaller) → first-to-7 win-by-2 cap-10 → KDA log → kick all → 5-min break → repeat until `/scrim stop`.

## Decisions locked in (from Q&A)

| Topic | Decision |
|---|---|
| Runtime | Executor script on a private-server host account (single file, uses `request()`), firing the game's own remotes |
| Transport | Script polls Flask (`atlclips.site`) every ~2s; Flask reads/writes Mongo; bot cog reads Mongo. Auth = `ROBLOX_WEBHOOK_SECRET` header |
| Verified = | A `roblox_usernames` doc exists with `roblox_id == player.UserId` (match on UserId, never username) |
| Verify check | Continuously in lobby (unverified never count toward 10 or reset the timer) + full recheck at lock |
| Timer | 30s. Each *join* of a verified player resets it. Resets stop once 10 verified are in. Leaves never reset |
| Under 10 at timer end | Start if >= `min_players` (default 6), even teams (`floor(n/2)` per side, odd leftover kicked); else keep waiting |
| Teams | Random pick in script. Black/White are labels only (nametags + KDA); game's `Playing` team is used for round detection |
| Series | Over when `max>=10` OR (`max>=7` AND lead>=2). 9-9 -> next point wins 10-9 |
| Mid-match | Leaver = dead for the rest of the match and cannot return; new joiners kicked ("match in progress"); empty team forfeits (flagged `forfeit`) |
| Post-match | Log embed + Mongo doc -> kick everyone (1/sec) -> 5-min break -> bot re-arms the same lobby message |
| Rate limit | Every game remote (kick, announce, start/stop) goes through one queue, >= 1.0s apart, announces get priority |
| Permissions | Head Observer + Co-owner + master admin (existing role checks) |
| Stats | One embed per match in log channel + saved doc (reuse `roblox_matches`) |
| Kick remote | `KickPlayer(userId, 5)`: meaning of `5` and reason support **unknown** -> constants + `DRY_RUN` flag + in-game test |

## Architecture (hybrid: script = real-time referee, bot = brain/persistence)

- **Script owns** anything latency-sensitive: countdown, kick/announce queue, roster tracking, team pick, round detection, bomb-chain KDA, win-by-2 logic.
- **Bot owns** lifecycle, Discord UI, verification data, scheduling (5-min break), permissions, logging, crash recovery.
- Script sends a **full snapshot** every sync (roster, phase, score, stats, `match_id`). Bot can restart at any time and lose nothing. Bot replies with **commands** (`arm_lobby`, `stop`, `kick_all`) and the **verified set** for the UserIds reported.

State machine (persisted in `scrim_sessions`): `IDLE -> LOBBY -> COUNTDOWN -> LIVE -> POSTGAME -> BREAK -> LOBBY ...`, `STOP` from anywhere.

## Files

**Bot (Python)**
- `core/services/scrim_rules.py` – pure: `is_match_over(a, b)`, `team_sizes(n, min_players)`, `pick_teams(...)` (testable, shared test vectors with Luau)
- `core/services/scrim_service.py` – state machine, command queue, heartbeat watchdog
- `database/scrim.py` – `scrim_sessions`, `scrim_commands` (id, status, ack), index on `roblox_usernames.roblox_id`
- `cogs/scrim.py` – `/scrim start link`, `/scrim stop`, `/scrim status`, `/scrim config` (lobby channel, log channel, min players, break minutes); lobby embed + link button; log embed. Register in `main.py`
- `config.py` – optional env fallbacks `SCRIM_LOBBY_CHANNEL_ID`, `SCRIM_LOG_CHANNEL_ID`

**Flask (`clips/api/scrim.py`)** – `POST /api/scrim/sync` (auth via `hmac.compare_digest`), `POST /api/scrim/match-final` (idempotent on `match_id`)

**Roblox** – extend `roblox_script.md` (single file): `http()` wrapper (`request`/`http_request`, pcall, backoff), `ActionQueue`, `Lobby` module, sync loop, hooks into existing death/bomb code, `Rules` mirror of `scrim_rules`. `POINTS_TO_WIN=3` replaced by rules. Destroy UI also stops the agent and notifies the bot.

## Lobby/UI behaviour
- One lobby message, edited (debounced >= 3s). Countdown shown with Discord `<t:ts:R>` so no per-second edits.
- Roster shows only verified players; unverified are listed as "kicked: unverified".
- Join = link button; swapped for a disabled "Match in progress" button while LIVE.
- Embed footer: verify link (`/api/roblox/login`). In-game announce on unverified kick tells them to verify first.
- Watchdog: no sync for 20s -> embed shows HOST OFFLINE and pings Head Observers once.

## Edge cases handled
1. Players pending kick are excluded from counts (no double-kick, no timer resets from kicked joiners).
2. Verified players joining during BREAK are not kicked; counted when lobby re-arms.
3. Both teams wiped same moment: existing last-bomb-holder tiebreak; otherwise redo round, no point.
4. Host script restart mid-match: match marked `aborted`, partial stats logged and flagged, lobby re-armed.
5. Duplicate/late `match-final` posts ignored via `match_id`.
6. Roblox UserId type mismatch (`sub` is a string) – query both `str` and `int`.
7. Invalid links rejected (must be `roblox.com` with `privateServerLinkCode`).

## Recommended hardening (separate from the feature, flag before shipping)
- `/api/roblox/login` uses raw `state=<discord_id>`: anyone can link their Roblox account to someone else's Discord ID and overwrite it, which can leave the victim "unverified". Fix: HMAC-signed `state` + expiry.
- Enforce one Discord ID per `roblox_id` (unique index) to stop alt sharing.
- Existing `/api/roblox/live-update` and `/match-stats` appear unauthenticated in `clips/api/roblox.py`; confirm and add the same secret check.

## Test plan
- pytest: rules vectors (7-0, 7-5, 7-6 continues, 8-6, 9-9 -> 10-9, 10-8 impossible), team sizing, state machine with fake clock, endpoint auth, verified lookup.
- Script `DRY_RUN=true`: logs every remote instead of firing; verify 1s spacing and ordering.
- Manual E2E on a private server with 2 alts: verify/unverified kick, timer reset, 10-lock, surplus kick, leave/forfeit, post-match kick-all, 5-min re-arm.

## Open inputs still needed (non-blocking until implementation)
- Lobby channel ID and log channel ID (log ID promised later).
- Result of the `KickPlayer` duration/reason test.
- Verify-grace window before an unverified player is kicked (recommend 20s with announce; 0 = instant).

## Implementation notes (as built, 2026-10-10)
- Transport: the script posts to the **bot's own aiohttp server** (`POST /api/scrim/sync`, `X-Roblox-Secret`), not the Flask site. No Mongo hop, no command queue: the sync response carries `session`, `verified`, `config`, `acked`.
- Matches are stored in `scrim_matches` (not `roblox_matches`), keyed by `match_id` so they are recorded exactly once. Aborted matches (host dropped mid-match) are detected from the LIVE -> non-LIVE snapshot transition.
- Channel IDs come from Mongo `config` doc `api_keys`: `SCRIM_ANC_ID` (lobby) and `SCRIM_LOG_ID` (results).
- OAuth hardening shipped: `/link_roblox` issues an HMAC-signed, 10-minute `state`; `clips/api/roblox.py` rejects unsigned/expired state and refuses a Roblox account already linked to another Discord ID.
- Needs deploy-time setup: `ROBLOX_OAUTH_STATE_SECRET` (same value on bot and clips service), and `API_URL` / `API_SECRET` at the top of `roblox_script.md`.
- Timeout update: game remotes are `KickPlayer(userId, 5)` (5 = lockout minutes, `false` = plain kick) and `ChangeTimeout(userId, true|false)` (sit out, persists across rejoin). Verified players not picked are timed out at lock; everyone's timeout is released right before the post-match kick-all. Unverified and mid-match joiners are kicked (5 min). The bot persists the timed-out IDs (`timed_out` on the session) and hands them back to a restarted host script so nobody stays stuck.
- Captains update: with `/scrim config captains:True` (default) the lock is followed by two single-round free-for-all bomb rounds (last player standing = captain; round-1 winner is timed out for round 2; ties replay twice then random among the tied), then a Discord draft. Black = round-1 captain and picks first, strict alternation, 30s per pick (`pick_seconds`), auto-random on expiry, staff can pick for an absent captain. The dropdown lives on the lobby message in the SCRIM_ANC_ID channel and only the current captain's linked Discord account (or staff) can use it. Everyone except the captains is timed out during the draft; each pick is released immediately; unpicked players stay timed out; uneven teams (leavers) are trimmed. A captain who leaves is replaced by a random remaining player. `captains:False` falls back to random teams. The bot owns the draft (`session.draft`); the script only reports captains + pool and applies picks idempotently by `seq`.
- Mongo bridge (bot hosted by someone else): the script posts to `clip-hosting` (`POST /api/scrim/sync`, secret from env or Mongo `config.api_keys.ROBLOX_WEBHOOK_SECRET`). The site parks the snapshot in `scrim_inbox`; the bot's `bridge_loop` (0.5s) answers through `scrim_outbox` (TTL 120s, stale requests > 15s dropped). `/link_roblox` and the site resolve the OAuth state secret identically (`resolve_state_secret`: ROBLOX_OAUTH_STATE_SECRET, falling back to ROBLOX_WEBHOOK_SECRET, env first then Mongo). The direct bot endpoint still exists but is unused.
