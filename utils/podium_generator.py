import os
import io
import asyncio
import hashlib
from typing import List, Tuple, Optional
import aiohttp
from PIL import Image, ImageDraw

CACHE_DIR = "assets/cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def crop_to_circle(im: Image.Image, size: int = 150) -> Image.Image:
    """Crop an image to a circle."""
    im = im.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
    mask = Image.new("L", im.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0) + im.size, fill=255)
    
    output = Image.new("RGBA", im.size, (0, 0, 0, 0))
    output.paste(im, (0, 0), mask=mask)
    return output

def draw_simple_podium(tier_name: str, avatars: List[Optional[Image.Image]]) -> io.BytesIO:
    """Draws the podium and pastes the avatars."""
    width, height = 800, 600
    bg_color = (43, 45, 49) # Discord dark theme bg
    img = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    
    # 3 equal columns
    col_width = width / 3
    
    # Y offsets representing the top Y coordinate of each podium block
    y_1 = int(height * 0.55) # Top of gold
    y_2 = int(height * 0.65) # Top of silver
    y_3 = int(height * 0.72) # Top of bronze
    
    # Colors that look good in dark mode
    gold = (212, 175, 55)   # Metallic Gold
    silver = (170, 170, 170) # Silver/Grey
    bronze = (165, 113, 100) # Bronze
    
    # Draw Silver (Left: 0 to col_width)
    draw.rectangle([0, y_2, col_width, height], fill=silver)
    # Draw Gold (Center: col_width to 2*col_width)
    draw.rectangle([col_width, y_1, 2 * col_width, height], fill=gold)
    # Draw Bronze (Right: 2*col_width to width)
    draw.rectangle([2 * col_width, y_3, width, height], fill=bronze)
    
    # Centers for avatars
    center_1 = int(1.5 * col_width) # Center of gold
    center_2 = int(0.5 * col_width) # Center of silver
    center_3 = int(2.5 * col_width) # Center of bronze
    
    centers = [center_1, center_2, center_3]
    avatar_y_offsets = [y_1, y_2, y_3]
    
    # Draw title
    draw.text((width//2, 40), f"{tier_name} Top 3", fill="white", anchor="mm", font_size=56)
    
    # 1st place is bigger
    avatar_sizes = [240, 160, 160]
    
    # We expect avatars in order: 1st, 2nd, 3rd
    for i, avatar in enumerate(avatars):
        if avatar:
            size = avatar_sizes[i]
            avatar = crop_to_circle(avatar, size)
            
            # Paste so bottom of avatar overlaps top of podium slightly
            paste_x = centers[i] - (size // 2)
            paste_y = avatar_y_offsets[i] - size + 15
            
            # Ensure it doesn't paste out of bounds negatively
            paste_y = max(0, paste_y)
            img.paste(avatar, (paste_x, paste_y), mask=avatar)
            
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

async def download_avatar(session: aiohttp.ClientSession, url: str) -> Optional[Image.Image]:
    if not url:
        return None
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
                return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        print(f"Failed to download avatar: {e}")
    return None

async def get_podium_image(tier_name: str, top_3: List[Tuple[int, str]]) -> str:
    """
    Returns the path to the cached podium image.
    top_3 is a list of tuples: (user_id, avatar_url) ordered 1st, 2nd, 3rd.
    If a spot is empty, the tuple can be (0, "")
    """
    # Create cache key
    hash_str = f"{tier_name}"
    for uid, url in top_3:
        hash_str += f"_{uid}_{url}"
        
    cache_key = hashlib.md5(hash_str.encode()).hexdigest()
    file_path = os.path.join(CACHE_DIR, f"podium_{tier_name}_{cache_key}.png")
    
    if os.path.exists(file_path):
        return file_path
        
    # Not in cache, generate it
    async with aiohttp.ClientSession() as session:
        tasks = [download_avatar(session, url) for uid, url in top_3]
        avatars = await asyncio.gather(*tasks)
        
    loop = asyncio.get_running_loop()
    buffer = await loop.run_in_executor(None, draw_simple_podium, tier_name, avatars)
    
    with open(file_path, "wb") as f:
        f.write(buffer.getbuffer())
        
    return file_path
