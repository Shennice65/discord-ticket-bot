import re
import aiohttp
from typing import Dict

MEDAL_URL_PATTERN = re.compile(r'^https?://(www\.)?medal\.tv/.+', re.IGNORECASE)
TIKTOK_URL_PATTERN = re.compile(
    r'^https?://(www\.)?(tiktok\.com/.+|vm\.tiktok\.com/.+)',
    re.IGNORECASE
)

# OG tag patterns — simple regex to avoid requiring beautifulsoup dependency
OG_TITLE_PATTERN = re.compile(
    r'<meta\s+(?:property=["\']og:title["\']\s+content=["\']([^"\']*)["\']|content=["\']([^"\']*)["\']?\s+property=["\']og:title["\'])',
    re.IGNORECASE
)
OG_IMAGE_PATTERN = re.compile(
    r'<meta\s+(?:property=["\']og:image["\']\s+content=["\']([^"\']*)["\']|content=["\']([^"\']*)["\']?\s+property=["\']og:image["\'])',
    re.IGNORECASE
)
OG_VIDEO_PATTERN = re.compile(
    r'<meta\s+(?:property=["\']og:video(?::url)?["\']\s+content=["\']([^"\']*)["\']|content=["\']([^"\']*)["\']?\s+property=["\']og:video(?::url)?["\'])',
    re.IGNORECASE
)


def is_valid_medal_url(url: str) -> bool:
    """Check if the URL matches the Medal.tv domain pattern."""
    return bool(MEDAL_URL_PATTERN.match(url.strip()))


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


async def validate_and_scrape_medal(url: str) -> Dict:
    """Validate a Medal URL resolves and scrape OG metadata.
    
    Returns dict with keys: valid, title, thumbnail, error
    """
    url = url.strip()
    
    if not is_valid_medal_url(url):
        return {"valid": False, "title": "", "thumbnail": "", "error": "Not a valid Medal.tv URL"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as resp:
                if resp.status != 200:
                    return {"valid": False, "title": "", "thumbnail": "", "error": f"Medal link returned status {resp.status}"}
                
                html_content = await resp.text()
                
                # Extract OG title
                title_match = OG_TITLE_PATTERN.search(html_content)
                title = ""
                if title_match:
                    title = title_match.group(1) or title_match.group(2) or ""
                
                import html as html_lib
                
                # Extract OG image
                image_match = OG_IMAGE_PATTERN.search(html_content)
                thumbnail = ""
                if image_match:
                    thumbnail = html_lib.unescape(image_match.group(1) or image_match.group(2) or "")
                
                # Extract OG video URL (raw mp4)
                video_match = OG_VIDEO_PATTERN.search(html_content)
                video_url = ""
                if video_match:
                    video_url = html_lib.unescape(video_match.group(1) or video_match.group(2) or "")
                
                return {"valid": True, "title": title, "thumbnail": thumbnail, "video_url": video_url, "error": ""}
                
    except aiohttp.ClientError:
        return {"valid": False, "title": "", "thumbnail": "", "error": "Could not connect to Medal.tv"}
    except Exception as e:
        return {"valid": False, "title": "", "thumbnail": "", "error": f"Error: {str(e)}"}


async def convert_clip_via_service(url: str, service_base_url: str, title: str = "") -> Dict:
    """Send a URL to the clips conversion service and get back a task ID for polling.
    
    Returns dict with keys: success, task_id, error
    """
    if not service_base_url:
        return {"success": False, "task_id": "", "error": "Clips service URL not configured"}
    
    api_url = f"{service_base_url.rstrip('/')}/api/import"
    
    payload = {"url": url}
    if title:
        payload["title"] = title
        
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                
                if resp.status != 200 or not data.get("success"):
                    return {
                        "success": False,
                        "task_id": "",
                        "error": data.get("error", f"Service returned status {resp.status}")
                    }
                
                return {
                    "success": True,
                    "task_id": data["task_id"],
                    "error": ""
                }
    except aiohttp.ClientError as e:
        return {"success": False, "task_id": "", "error": f"Could not connect to clips service: {str(e)}"}
    except Exception as e:
        return {"success": False, "task_id": "", "error": f"Error: {str(e)}"}


async def check_clip_progress(task_id: str, service_base_url: str) -> Dict:
    """Check the progress of a clip import task.
    
    Returns dict with keys: success, progress_data, error
    """
    if not service_base_url or not task_id:
        return {"success": False, "error": "Invalid service URL or task ID"}
        
    api_url = f"{service_base_url.rstrip('/')}/api/progress/{task_id}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                data = await resp.json()
                
                if resp.status != 200 or not data.get("success"):
                    return {"success": False, "error": data.get("error", "Failed to get progress")}
                
                return {"success": True, "progress_data": data["progress_data"], "error": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def delete_clip_from_service(clip_page_url: str, service_base_url: str, admin_password: str) -> bool:
    """Send a request to the backend service to completely delete a clip from Cloudflare R2."""
    if not clip_page_url or not service_base_url:
        return False
        
    try:
        filename = clip_page_url.split('/')[-1]
        api_url = f"{service_base_url.rstrip('/')}/api/delete/{filename}"
        
        headers = {}
        if admin_password:
            headers["Authorization"] = f"Bearer {admin_password}"
            
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                api_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
                return data.get("success", False)
    except Exception as e:
        print(f"Error deleting from service: {e}")
        return False


def format_clip_display(clip_data: Dict, current_index: int, total_clips: int) -> str:
    """Format a clip for display in Discord messages, including title, URL, and upload date."""
    content_url = clip_data.get("clip_page_url") or clip_data.get("url", "")
    title = clip_data.get("title", "Untitled Clip")
    
    date_str = ""
    submitted_at = clip_data.get("submitted_at")
    if submitted_at:
        try:
            from datetime import datetime
            # Handle ISO format with Z or +00:00
            if submitted_at.endswith('Z'):
                submitted_at = submitted_at[:-1] + '+00:00'
            dt = datetime.fromisoformat(submitted_at)
            timestamp = int(dt.timestamp())
            # :d for date like '08/19/2026', or :R for '2 hours ago'
            # The user asked for "upload date", let's use <t:{timestamp}:D> (August 19, 2026) 
            date_str = f" • Uploaded <t:{timestamp}:d>"
        except Exception:
            pass
            
    # current_index is 0-based
    return f"**{title}**{date_str}\n{content_url}\n*Clip {current_index + 1} of {total_clips}*"
