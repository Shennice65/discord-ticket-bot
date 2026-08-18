import re
import aiohttp
from typing import Dict

MEDAL_URL_PATTERN = re.compile(r'^https?://(www\.)?medal\.tv/.+', re.IGNORECASE)

# OG tag patterns — simple regex to avoid requiring beautifulsoup dependency
OG_TITLE_PATTERN = re.compile(
    r'<meta\s+(?:property=["\']og:title["\']\s+content=["\']([^"\']*)["\']|content=["\']([^"\']*)["\']?\s+property=["\']og:title["\'])',
    re.IGNORECASE
)
OG_IMAGE_PATTERN = re.compile(
    r'<meta\s+(?:property=["\']og:image["\']\s+content=["\']([^"\']*)["\']|content=["\']([^"\']*)["\']?\s+property=["\']og:image["\'])',
    re.IGNORECASE
)


def is_valid_medal_url(url: str) -> bool:
    """Check if the URL matches the Medal.tv domain pattern."""
    return bool(MEDAL_URL_PATTERN.match(url.strip()))


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
                
                html = await resp.text()
                
                # Extract OG title
                title_match = OG_TITLE_PATTERN.search(html)
                title = ""
                if title_match:
                    title = title_match.group(1) or title_match.group(2) or ""
                
                # Extract OG image
                image_match = OG_IMAGE_PATTERN.search(html)
                thumbnail = ""
                if image_match:
                    thumbnail = image_match.group(1) or image_match.group(2) or ""
                
                return {"valid": True, "title": title, "thumbnail": thumbnail, "error": ""}
                
    except aiohttp.ClientError:
        return {"valid": False, "title": "", "thumbnail": "", "error": "Could not connect to Medal.tv"}
    except Exception as e:
        return {"valid": False, "title": "", "thumbnail": "", "error": f"Error: {str(e)}"}
