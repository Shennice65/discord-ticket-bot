# Leaderboard Avatar Toggle Design

## Purpose
Allow a master admin to toggle the leaderboard top 3 podium avatars between Discord profile pictures and Roblox avatar headshots. The command to toggle this must be exclusive and named `/togglelb3s`.

## Architecture
1. **Database / State:**
   - We will store a global setting `use_roblox_avatars` in the `bot_settings` collection using `SettingsMixin.set_setting` / `get_setting`.
   - By default, this will be `False` (use Discord avatars) or default to `True` if the user wants it.
   
2. **Commands:**
   - Create a new command `/togglelb3s` in `cogs/ranking/admin.py` (or `core.py`).
   - The command will check if `interaction.user.id == Config.MASTER_ADMIN_ID`. If not, it will return an ephemeral "You do not have permission" error.
   - If authorized, it toggles the `use_roblox_avatars` boolean in `bot_settings`, clears any cached podium images if necessary, and responds with the new state.

3. **Avatar Resolution (RankingService):**
   - In `RankingService.generate_leaderboard_content`, before getting the avatars for the top 3, fetch the `use_roblox_avatars` setting.
   - If `True`:
     - Use the existing Bloxlink integration to resolve the user's `robloxID`.
     - We will need to update `RankingService.get_roblox_username` or extract a `get_roblox_id` helper to return the numeric ID, not just the string username.
     - Once we have the `robloxID`, construct the Roblox thumbnail URL:
       `https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={robloxID}&size=150x150&format=Png&isCircular=false`
     - Make an HTTP GET request to this URL, extract the `imageUrl` from the response JSON (`data[0]["imageUrl"]`), and pass that to the podium generator.
     - Fallback: If the user hasn't linked their Roblox account, fallback to their Discord avatar.
   - If `False`:
     - Keep the existing behavior (use `user.display_avatar.url`).

4. **Podium Caching (PodiumGenerator):**
   - The cache key in `get_podium_image` already hashes the avatar URLs, so if the avatar URL changes from a Discord URL to a Roblox URL, it will naturally generate a new cache key and image without us needing to manually flush the cache.

## Potential Edge Cases
- **Roblox API Limits:** Caching the avatar URLs (or relying on the podium cache) prevents spamming the Roblox API. The podium cache key relies on the URL, so it's already well-optimized.
- **Unlinked Users:** Graceful fallback to Discord avatars ensures the podium never breaks.
