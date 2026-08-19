# Discord Clips System Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the clips conversion service (Flask on Render) into the Discord bot so players can submit Medal.tv or TikTok clips, which get converted/hosted via R2, and displayed as embeddable video players in Discord — all stored in the player's history Clips tab.

**Architecture:** The bot's `SubmitClipModal` sends the user's link to the Render service's new `/api/import` JSON endpoint. The service downloads the video via yt-dlp, converts to mp4, uploads to R2, and returns a JSON response with the clip page URL (which has OG video embeds Discord auto-previews). The bot stores both the original source URL and the embeddable clip page URL in MongoDB. The clip page URL is sent as message content so Discord renders its inline video player.

**Tech Stack:** Python 3.10+, discord.py v2.0+, Flask, yt-dlp, aiohttp, MongoDB (motor)

## Global Constraints

- Python 3.10+ compatible syntax
- `CLIPS_SERVICE_URL` env var holds the Render base URL (e.g. `https://your-app.onrender.com`)
- Password requirement removed from all clips service endpoints
- Max 5 clips per user (existing limit, unchanged)
- Supported clip sources: Medal.tv (`medal.tv`), TikTok (`tiktok.com`, `vm.tiktok.com`)

---

### Task 1: Remove Password from Clips Service & Add JSON API Endpoint

**Files:**
- Modify: `clips/app.py`

**Interfaces:**
- Produces: `POST /api/import` — accepts `{"url": "https://..."}`, returns `{"success": true, "clip_url": "https://.../clip/hash.mp4", "title": "...", "thumbnail_url": "https://.../photo/hash.jpg"}` or `{"success": false, "error": "..."}`

- [ ] **Step 1: Remove password checks from all endpoints**

In `clips/app.py`, remove the `PASSWORD` variable and all password checks from `/upload`, `/import_url`, `/api/clips`, `/api/clip/<filename>/edit`, and `/checkpassword`. The form endpoints (`/upload`, `/import_url`) should process the request directly without the password guard. Remove the `/checkpassword` endpoint entirely.

Remove these lines at the top:
```python
PASSWORD = os.environ["password"]
```

In `/upload` (line 60-107), change from:
```python
@app.route("/upload", methods=["POST"])
def upload():
    if request.form.get("password") == PASSWORD:
        file = request.files["file"]
        # ... all the upload logic ...
        return redirect(url_for("clip", filename=filename))
    else:
        flash("Incorrect Password")
        return redirect("/")
```
To (remove the if/else, dedent the body):
```python
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]
    hash = hashlib.md5(file.stream.read()).hexdigest()
    file.stream.seek(0)
    extension = "." + file.filename.split(".")[-1]
    name = file.filename.removesuffix(extension)
    if extension not in ALLOWED_EXTENSIONS:
        flash("Invalid filetype.")
        return redirect("/")

    file.save(os.path.join(UPLOAD_DIR, hash + extension))
    file.close()
    
    process_video(os.path.join(UPLOAD_DIR, hash + extension))
    extension = ".mp4"
    
    write_first_frame(UPLOAD_DIR, hash, extension)
    
    is_public = request.form.get("public")
    is_public = is_public if is_public else "false"
    
    metadata = {"title": name, "public": is_public, "extension": extension}
    metadata_path = os.path.join(UPLOAD_DIR, hash + ".json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f)

    upload_to_r2(os.path.join(UPLOAD_DIR, hash + extension), hash + extension)
    upload_to_r2(os.path.join(UPLOAD_DIR, hash + ".jpg"), hash + ".jpg")
    upload_to_r2(metadata_path, hash + ".json")

    os.remove(os.path.join(UPLOAD_DIR, hash + extension))
    os.remove(os.path.join(UPLOAD_DIR, hash + ".jpg"))
    os.remove(metadata_path)

    filename = hash + extension
    cache_clip(filename, name, is_public)

    return redirect(url_for("clip", filename=filename))
```

Apply the same pattern to `/import_url` (line 109-172) — remove the `if password == PASSWORD` guard and dedent. 

In `/api/clips` (line 191-198), remove the `admin` password check — just return all clips (or only public ones):
```python
@app.route("/api/clips")
def clips_api():
    return [
        {filename: info}
        for filename, info in clips_cache.items()
    ]
```

In `/api/clip/<filename>/edit` (line 211-230), remove the password check:
```python
@app.route("/api/clip/<filename>/edit", methods=["POST"])
def edit_clip(filename: str):
    extension = "." + filename.split(".")[-1]
    hash = filename.removesuffix(extension)
    title = request.form.get("title")
    public = request.form.get("public")
    # ... rest stays the same
```

Remove the `/checkpassword` endpoint entirely (lines 236-241).

- [ ] **Step 2: Add the `/api/import` JSON endpoint**

Add this new endpoint to `clips/app.py`, right after the existing `/import_url` endpoint:

```python
from flask import jsonify

@app.route("/api/import", methods=["POST"])
def api_import():
    """JSON API for bot integration. Accepts {"url": "..."}, returns clip details."""
    data = request.get_json(silent=True)
    if not data or not data.get("url"):
        return jsonify({"success": False, "error": "No URL provided"}), 400
    
    url = data["url"]
    
    try:
        ydl_opts = {
            'format': 'bestvideo[ext=mp4][filesize<50M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<50M]/best[filesize<50M]/best',
            'outtmpl': os.path.join(UPLOAD_DIR, 'tmp_dl_%(id)s.%(ext)s'),
            'quiet': True,
            'max_filesize': 50 * 1024 * 1024,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Imported Clip')
            downloaded_file = ydl.prepare_filename(info)
        
        with open(downloaded_file, 'rb') as f:
            hash_val = hashlib.md5(f.read()).hexdigest()
        
        orig_extension = "." + downloaded_file.split('.')[-1]
        new_filepath = os.path.join(UPLOAD_DIR, hash_val + orig_extension)
        os.replace(downloaded_file, new_filepath)
        
        process_video(new_filepath)
        extension = ".mp4"
        
        write_first_frame(UPLOAD_DIR, hash_val, extension)
        
        metadata = {"title": title, "public": "true", "extension": extension}
        metadata_path = os.path.join(UPLOAD_DIR, hash_val + ".json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f)

        upload_to_r2(os.path.join(UPLOAD_DIR, hash_val + extension), hash_val + extension)
        upload_to_r2(os.path.join(UPLOAD_DIR, hash_val + ".jpg"), hash_val + ".jpg")
        upload_to_r2(metadata_path, hash_val + ".json")

        os.remove(os.path.join(UPLOAD_DIR, hash_val + extension))
        os.remove(os.path.join(UPLOAD_DIR, hash_val + ".jpg"))
        os.remove(metadata_path)

        filename = hash_val + extension
        cache_clip(filename, title, "true")
        
        # Build the clip page URL (the one with OG tags for Discord embeds)
        clip_page_url = url_for("clip", filename=filename, _external=True)
        r2_domain = os.environ.get("R2_PUBLIC_DOMAIN", "")
        thumbnail_url = f"{r2_domain}/{hash_val}.jpg"
        
        return jsonify({
            "success": True,
            "clip_url": clip_page_url,
            "title": title,
            "thumbnail_url": thumbnail_url,
            "filename": filename
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
```

- [ ] **Step 3: Add `jsonify` import if not already present**

Ensure `from flask import jsonify` is included in the imports at the top of `clips/app.py`. Update line 1-2 to:
```python
from flask import Flask, send_file, jsonify
from flask import render_template, request, redirect, flash, url_for, send_from_directory
```

- [ ] **Step 4: Commit**

```bash
git add clips/app.py
git commit -m "feat(clips): remove password auth, add /api/import JSON endpoint for bot integration"
```

---

### Task 2: Add TikTok URL Validation & Clip Conversion Client to Bot

**Files:**
- Modify: `utils/clips_utils.py`
- Modify: `config.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `CLIPS_SERVICE_URL` from `Config`
- Produces: `is_valid_clip_url(url) -> bool`, `get_clip_source(url) -> str`, `convert_clip_via_service(url: str) -> Dict`

- [ ] **Step 1: Add `CLIPS_SERVICE_URL` to config and .env.example**

In `config.py`, add inside the `Config` class:
```python
CLIPS_SERVICE_URL = os.environ.get('CLIPS_SERVICE_URL', '')
```

In `.env.example`, append:
```
# Clips Service (Render)
CLIPS_SERVICE_URL=https://your-clips-app.onrender.com
```

- [ ] **Step 2: Add TikTok URL pattern and validation functions to `utils/clips_utils.py`**

Add after the existing `MEDAL_URL_PATTERN` (line 5):
```python
TIKTOK_URL_PATTERN = re.compile(
    r'^https?://(www\.)?(tiktok\.com/.+|vm\.tiktok\.com/.+)',
    re.IGNORECASE
)
```

Add these new functions after `is_valid_medal_url`:
```python
def is_valid_tiktok_url(url: str) -> bool:
    """Check if the URL matches TikTok domain patterns."""
    return bool(TIKTOK_URL_PATTERN.match(url.strip()))


def is_valid_clip_url(url: str) -> bool:
    """Check if the URL is a supported clip source (Medal.tv or TikTok)."""
    return is_valid_medal_url(url) or is_valid_tiktok_url(url)


def get_clip_source(url: str) -> str:
    """Return the source platform name, or 'unknown'."""
    url = url.strip()
    if is_valid_medal_url(url):
        return "medal"
    elif is_valid_tiktok_url(url):
        return "tiktok"
    return "unknown"
```

- [ ] **Step 3: Add `convert_clip_via_service()` function**

Add at the bottom of `utils/clips_utils.py`:
```python
async def convert_clip_via_service(url: str, service_base_url: str) -> Dict:
    """Send a URL to the clips conversion service and get back the embeddable clip URL.
    
    Returns dict with keys: success, clip_url, title, thumbnail_url, error
    """
    if not service_base_url:
        return {"success": False, "clip_url": "", "title": "", "thumbnail_url": "", "error": "Clips service URL not configured"}
    
    api_url = f"{service_base_url.rstrip('/')}/api/import"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                json={"url": url},
                timeout=aiohttp.ClientTimeout(total=120)  # Video processing can be slow
            ) as resp:
                data = await resp.json()
                
                if resp.status != 200 or not data.get("success"):
                    return {
                        "success": False,
                        "clip_url": "",
                        "title": "",
                        "thumbnail_url": "",
                        "error": data.get("error", f"Service returned status {resp.status}")
                    }
                
                return {
                    "success": True,
                    "clip_url": data["clip_url"],
                    "title": data.get("title", "Imported Clip"),
                    "thumbnail_url": data.get("thumbnail_url", ""),
                    "error": ""
                }
    except aiohttp.ClientError as e:
        return {"success": False, "clip_url": "", "title": "", "thumbnail_url": "", "error": f"Could not connect to clips service: {str(e)}"}
    except Exception as e:
        return {"success": False, "clip_url": "", "title": "", "thumbnail_url": "", "error": f"Error: {str(e)}"}
```

- [ ] **Step 4: Commit**

```bash
git add utils/clips_utils.py config.py .env.example
git commit -m "feat(clips): add TikTok URL validation and clips service client"
```

---

### Task 3: Update Database Schema to Store Clip Page URL

**Files:**
- Modify: `database/clips.py`

**Interfaces:**
- Consumes: `clip_page_url` (str) from callers
- Produces: `add_user_clip(user_id, url, title, thumbnail, clip_page_url) -> bool` — updated signature with new parameter

- [ ] **Step 1: Update `add_user_clip` to accept and store `clip_page_url`**

In `database/clips.py`, update the `add_user_clip` method (line 22-40):

```python
async def add_user_clip(self, user_id: int, url: str, title: str, thumbnail: str, clip_page_url: str = "") -> bool:
    """Add a clip to a user's collection. Returns False if at max limit."""
    current_count = await self.get_user_clip_count(user_id)
    if current_count >= MAX_CLIPS_PER_USER:
        return False

    clip = {
        "url": url,
        "title": title or "Untitled Clip",
        "thumbnail": thumbnail or "",
        "clip_page_url": clip_page_url or "",
        "submitted_at": datetime.now(timezone.utc).isoformat()
    }

    await self.db.player_clips.update_one(
        {"user_id": user_id},
        {"$push": {"clips": clip}},
        upsert=True
    )
    return True
```

The key change: new `clip_page_url` parameter (defaulting to `""` for backwards compatibility with existing clips) and storing it in the clip document.

- [ ] **Step 2: Commit**

```bash
git add database/clips.py
git commit -m "feat(clips): add clip_page_url field to clip schema"
```

---

### Task 4: Update Submit Modal & Embeds for Medal + TikTok + Conversion Service

**Files:**
- Modify: `cogs/ranking/history.py`
- Modify: `utils/embeds.py`

**Interfaces:**
- Consumes: `is_valid_clip_url()`, `get_clip_source()`, `convert_clip_via_service()` from `utils/clips_utils.py`
- Consumes: `Config.CLIPS_SERVICE_URL` from `config.py`
- Consumes: `db.add_user_clip(user_id, url, title, thumbnail, clip_page_url)` from `database/clips.py`

- [ ] **Step 1: Update imports in `cogs/ranking/history.py`**

Change line 12:
```python
from utils.clips_utils import validate_and_scrape_medal
```
To:
```python
from utils.clips_utils import is_valid_clip_url, get_clip_source, convert_clip_via_service
```

- [ ] **Step 2: Rewrite `SubmitClipModal` to support Medal + TikTok via conversion service**

Replace the entire `SubmitClipModal` class (lines 115-166) with:

```python
class SubmitClipModal(discord.ui.Modal, title="Submit a Clip"):
    def __init__(self, target_user: discord.Member, clips_view_ref):
        super().__init__()
        self.target_user = target_user
        self.clips_view_ref = clips_view_ref
        
        self.clip_url = discord.ui.TextInput(
            label="Medal.tv or TikTok Link",
            placeholder="https://medal.tv/clips/... or https://tiktok.com/...",
            required=True,
            max_length=500
        )
        self.add_item(self.clip_url)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        url = self.clip_url.value.strip()
        db = interaction.client.db
        
        # Check clip limit
        count = await db.get_user_clip_count(self.target_user.id)
        if count >= 5:
            await interaction.followup.send("You've reached the maximum of **5 clips**. Delete one first!", ephemeral=True)
            return
        
        # Validate URL is Medal or TikTok
        if not is_valid_clip_url(url):
            await interaction.followup.send(
                "❌ Invalid link! Please submit a **Medal.tv** or **TikTok** URL.",
                ephemeral=True
            )
            return
        
        source = get_clip_source(url)
        
        # Send to conversion service
        await interaction.followup.send(
            f"⏳ Processing your {source.title()} clip... This may take a moment.",
            ephemeral=True
        )
        
        result = await convert_clip_via_service(url, Config.CLIPS_SERVICE_URL)
        
        if not result["success"]:
            await interaction.edit_original_response(
                content=f"❌ Failed to process clip: {result['error']}"
            )
            return
        
        # Store clip with both the original URL and the embeddable clip page URL
        success = await db.add_user_clip(
            self.target_user.id,
            url,
            result["title"],
            result["thumbnail_url"],
            result["clip_url"]
        )
        
        if not success:
            await interaction.edit_original_response(
                content="Failed to save clip. You may be at the limit."
            )
            return
        
        # Refresh the clips view
        clips = await db.get_user_clips(self.target_user.id)
        self.clips_view_ref.clips = clips
        self.clips_view_ref.current_page = len(clips) - 1
        
        clip = clips[-1]
        embed = TicketEmbeds.clips_embed(self.target_user, clip, len(clips) - 1, len(clips))
        self.clips_view_ref.update_buttons()
        
        # Use clip_page_url as content so Discord auto-embeds the video player
        content = clip.get("clip_page_url") or clip.get("url", "")
        await interaction.edit_original_response(content=content, embed=embed, view=self.clips_view_ref)
```

- [ ] **Step 3: Update `clips_embed` in `utils/embeds.py` to link to clip page**

Replace the `clips_embed` method (lines 432-457) with:

```python
@staticmethod
def clips_embed(user: discord.Member, clip: dict, page: int, total: int) -> discord.Embed:
    """Build a rich embed for a single clip page."""
    title = clip.get("title", "Untitled Clip")
    source_url = clip.get("url", "")
    clip_page_url = clip.get("clip_page_url", "")
    submitted_at = clip.get("submitted_at", "")

    # Link to the clip page (embeddable) if available, fall back to source
    display_url = clip_page_url or source_url

    embed = discord.Embed(
        title=title,
        url=display_url,
        color=discord.Color(0x2b2d31)
    )
    
    # Show source link if we have a clip page URL (so user can see original)
    if clip_page_url and source_url:
        embed.description = f"**[Watch Clip]({clip_page_url})**\n*[Original Source]({source_url})*"
    elif display_url:
        embed.description = f"**[Watch Clip]({display_url})**"

    if submitted_at:
        date_str = submitted_at[:10]
        embed.add_field(name="Submitted", value=date_str, inline=True)

    embed.set_author(name=f"{user.display_name}'s Clips", icon_url=user.display_avatar.url)
    embed.set_footer(text=f"Clip {page + 1} of {total} | User ID: {user.id}")
    
    thumbnail = clip.get("thumbnail")
    if thumbnail:
        embed.set_image(url=thumbnail)
        
    return embed
```

- [ ] **Step 4: Update navigation buttons in `ClipsPaginationView` to use `clip_page_url`**

In `cogs/ranking/history.py`, update the `btn_prev` callback (line 298-304) — change the content line:
```python
await interaction.edit_original_response(
    content=self.clips[self.current_page].get("clip_page_url") or self.clips[self.current_page].get("url", ""),
    embed=embed, view=self
)
```

Same change in `btn_next` callback (line 307-313):
```python
await interaction.edit_original_response(
    content=self.clips[self.current_page].get("clip_page_url") or self.clips[self.current_page].get("url", ""),
    embed=embed, view=self
)
```

- [ ] **Step 5: Update `DeleteClipSelect` and `DeleteClipModal` to use `clip_page_url`**

In `DeleteClipModal.on_submit` (line 213), update the content line:
```python
await interaction.edit_original_response(
    content=clips[self.clips_view_ref.current_page].get("clip_page_url") or clips[self.clips_view_ref.current_page].get("url", ""),
    embed=embed, view=self.clips_view_ref
)
```

In `DeleteClipSelect.callback` (line 264), same change:
```python
await interaction.edit_original_response(
    content=clips[self.clips_view_ref.current_page].get("clip_page_url") or clips[self.clips_view_ref.current_page].get("url", ""),
    embed=embed, view=self.clips_view_ref
)
```

- [ ] **Step 6: Update the `btn_clips` handler in `HistoryView` to use `clip_page_url`**

In `HistoryView.btn_clips` (line 361-363), update the content line:
```python
await interaction.followup.send(
    content=clips[0].get("clip_page_url") or clips[0].get("url", ""),
    embed=embed, view=clips_view, ephemeral=True
)
```

- [ ] **Step 7: Commit**

```bash
git add cogs/ranking/history.py utils/embeds.py
git commit -m "feat(clips): integrate conversion service, support Medal + TikTok submissions"
```

---

### Task 5: Update `.env.example` Documentation & Final Verification

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Verify `.env.example` has the new env var**

Confirm `.env.example` contains (added in Task 2):
```
# Clips Service (Render)
CLIPS_SERVICE_URL=https://your-clips-app.onrender.com
```

- [ ] **Step 2: Manual verification — test the full flow**

1. Start the clips service locally or verify it's deployed on Render
2. Start the Discord bot
3. Use `/history` → Click "Clips" → Click "Submit Clip"
4. Submit a Medal.tv link → verify processing message → verify clip appears with video embed
5. Submit a TikTok link → verify same flow
6. Submit an invalid URL → verify error message
7. Navigate between clips with Prev/Next → verify video embeds update
8. Delete a clip → verify it's removed

- [ ] **Step 3: Final commit**

```bash
git add .env.example
git commit -m "docs: update .env.example with CLIPS_SERVICE_URL"
```

---

## Verification Plan

### Manual Verification
- Submit a Medal.tv clip → confirm the bot shows "Processing..." then the converted clip with Discord video embed
- Submit a TikTok clip (both `tiktok.com` and `vm.tiktok.com` forms) → same flow works
- Submit an invalid URL → error message shown
- Try submitting a 6th clip → limit error shown
- Navigate clips → video embeds update correctly
- Delete a clip → works, view refreshes
- Old clips (without `clip_page_url`) still display correctly using fallback to `url`
