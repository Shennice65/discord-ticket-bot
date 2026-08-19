# Clips Feature — Design Spec

## Overview

Add a **Clips** tab to the `/history` command's `HistoryView`, allowing players to submit, browse, and manage their Medal.tv clip links. Clips are user-owned content — anyone can view them via `/history`, but only the clip owner can submit and delete.

## User Stories

1. **As a player**, I want to submit my Medal.tv clips so others can see my gameplay highlights.
2. **As a viewer**, I want to browse a player's clips with navigation buttons, one clip per page with rich previews.
3. **As a clip owner**, I want to delete my own clips by selecting them from a dropdown or entering the index number.

## Design Decisions

### Data Model

New MongoDB collection: `player_clips`

```json
{
  "user_id": 123456789,
  "clips": [
    {
      "url": "https://medal.tv/clips/abc123",
      "title": "Insane flick headshot",
      "thumbnail": "https://cdn.medal.tv/...",
      "submitted_at": "2026-08-18T12:00:00Z"
    }
  ]
}
```

- Indexed on `user_id` (unique)
- Max **5 clips** per player (enforced at application level)
- Clips stored as an embedded array (not a separate collection) since max 5 per user

### Medal Link Validation

1. **URL format check**: Must match `medal.tv` domain (regex: `https?://(www\.)?medal\.tv/.+`)
2. **HTTP resolution check**: HEAD/GET request to verify the link actually resolves (status 200)
3. **OpenGraph scraping**: Parse `og:title` and `og:image` meta tags from the page HTML for rich preview data

### UI Flow

#### Clips Button in HistoryView
- New button: `🎬 Clips` added to `HistoryView` (between Observations and Clear History)
- Clicking it shows the first clip page (or a "no clips" message)
- Shows clip embed with: thumbnail as embed image, title, URL as clickable link, submission date, page indicator

#### Submit Flow
- "Submit Clip" button visible **only** when the viewer is the clip owner (i.e., `interaction.user.id == target_user.id`)
- Opens a **Modal** with a single TextInput for the Medal.tv URL
- On submit: validates URL format → HTTP check → scrape OG data → store in DB → refresh view

#### Delete Flow
- "Delete Clip" button visible **only** to the clip owner
- Two delete methods:
  - **Select Menu**: Dropdown showing all clips (truncated title + index) to pick from
  - **Modal**: Text input to type the clip index number (1-5)
- After deletion: refresh the clip view

#### Navigation
- ◀️ Previous / ▶️ Next buttons for paging through clips (1 per page)
- Page indicator in embed footer: "Clip 1 of 3"
- Buttons disabled at boundaries (first/last page)

### Permissions

| Action | Who Can |
|--------|---------|
| View clips tab | Anyone who can use `/history` on that player |
| Submit clips | Only the clip owner (the target user viewing their own history) |
| Delete clips | Only the clip owner |

### Error Handling

- Invalid URL format → ephemeral error message
- URL doesn't resolve (404/timeout) → ephemeral error: "This Medal link doesn't seem to work"
- OG scrape fails → store clip with URL only, display without thumbnail/title
- Max clips reached (5) → ephemeral error: "You've reached the maximum of 5 clips"
- Delete index out of range → ephemeral error

### Architecture

Follows existing patterns:
- **Database**: New `ClipsMixin` in `database/clips.py` (mixin pattern matching `HistoryMixin`, `LadderMixin`, etc.)
- **View**: `ClipsPaginationView` in `cogs/ranking/history.py` (keeps history-related views together)
- **Embed**: New `clips_embed()` static method in `utils/embeds.py` (matches existing `history_*_embed` pattern)
- **No new cog or service** — clips integrate directly into the existing history flow

## Things to Watch Out For

1. **View timeout**: The `HistoryView` has a 180s timeout. The `ClipsPaginationView` spawned from it should have its own independent timeout.
2. **Rate limiting on OG scraping**: Medal.tv may rate-limit aggressive scraping. We only scrape on submit (max 5 times per user ever), so this is low risk.
3. **Stale thumbnails**: If Medal changes a clip's thumbnail URL, the stored URL may break. Consider a "refresh" mechanism later if this becomes a problem.
4. **Ephemeral message limitations**: Since `/history` responds ephemerally, all clip views are also ephemeral. This means Discord won't auto-embed Medal links — we MUST use embed images explicitly.
5. **aiohttp session management**: Use `aiohttp.ClientSession()` per-request (matching existing codebase pattern), not a persistent session.
6. **MongoDB document size**: With max 5 clips, each with ~500 bytes of metadata, the document stays well under MongoDB's 16MB limit.
