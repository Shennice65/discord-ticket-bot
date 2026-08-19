# Upload Clip Feature Design

## Purpose
Allow players to upload video clips directly from their devices instead of being limited to Medal.tv or TikTok links. Since Discord slash commands with modals do not support file attachments, we will introduce a new slash command for this purpose.

## Scope
- Add a new `/uploadclip` slash command to the Discord bot.
- Accept a video file attachment (subject to Discord's 25MB limit).
- Send the uploaded video to the existing conversion service, which will host it on R2.
- Save the resulting clip data to the user's history in the database, similar to how Medal/TikTok links are handled.

## Architecture & Data Flow
1. **User Action:** The user runs `/uploadclip` and attaches a video file.
2. **Validation:** 
   - Check if the user has reached the 5-clip limit.
   - Check if the attachment is a valid video file type (e.g., `video/mp4`, `video/quicktime`, `video/webm`).
3. **Processing:**
   - The bot replies with an ephemeral "Processing..." message.
   - The bot retrieves the Discord CDN URL for the attachment.
   - The bot sends a request to the conversion service with the Discord CDN URL. 
   - *Note: Since the Discord attachment is public while the message exists, the conversion service can download it directly from the URL without needing bot authentication tokens.*
4. **Polling:**
   - The bot polls the conversion service for progress using the `task_id`, just like it does for Medal links.
5. **Storage & Feedback:**
   - Once the conversion service completes, the bot saves the R2-hosted video URL and thumbnail to the database.
   - The bot updates the ephemeral message indicating success.

## Component Changes
- `cogs/ranking/history.py`: Add the `@app_commands.command(name="uploadclip")` command.
- `utils/clips_utils.py`: Might need a slight adjustment if the conversion service needs to know this is a direct video link rather than a Medal/TikTok URL. We need to verify how `convert_clip_via_service` handles direct `.mp4` URLs.

## Error Handling
- Invalid file types (e.g., images or documents) will be rejected immediately.
- If the conversion service fails to process the video, inform the user ephemerally.
- If the user has 5 clips, inform them they must delete one using `/history` before uploading more.

## Testing
- Ensure the slash command accepts the file correctly.
- Verify the conversion service can download from the Discord attachment URL.
- Check that the clip appears correctly in the `/history` Clips tab.
