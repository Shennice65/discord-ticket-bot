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
