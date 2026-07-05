import os
import io
import asyncio
import hashlib
from typing import List, Tuple, Optional
import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageChops

CACHE_DIR = "assets/cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# Load custom font
FONT_PATH = os.path.join("assets", "fonts", "Impact.ttf")
try:
    FONT_TITLE = ImageFont.truetype(FONT_PATH, 56)
    FONT_PLACE = ImageFont.truetype(FONT_PATH, 72)
except (OSError, IOError):
    FONT_TITLE = ImageFont.load_default()
    FONT_PLACE = ImageFont.load_default()

def crop_to_circle(im: Image.Image, size: int = 150) -> Image.Image:
    """Crop an image to a circle."""
    im = im.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
    mask = Image.new("L", im.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0) + im.size, fill=255)
    
    output = Image.new("RGBA", im.size, (0, 0, 0, 0))
    output.paste(im, (0, 0), mask=mask)
    return output

def draw_simple_podium(tier_name: str, avatars: List[Optional[Image.Image]], names: List[str]) -> io.BytesIO:
    """Draws the podium, applies texture, and pastes avatars/names."""
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
    
    # Draw podium blocks as solid rectangles
    col_w = int(col_width)
    draw.rectangle([0, y_2, col_w, height], fill=silver)
    draw.rectangle([col_w, y_1, 2 * col_w, height], fill=gold)
    draw.rectangle([2 * col_w, y_3, width, height], fill=bronze)
    
    # Centers for avatars
    center_1 = int(1.5 * col_width) # Center of gold
    center_2 = int(0.5 * col_width) # Center of silver
    center_3 = int(2.5 * col_width) # Center of bronze
    
    centers = [center_1, center_2, center_3]
    avatar_y_offsets = [y_1, y_2, y_3]
    
    # Draw title
    draw.text((width//2, 40), f"{tier_name} Top 3", fill="white", anchor="mm", font=FONT_TITLE)
    
    # 1st place is bigger
    avatar_sizes = [240, 160, 160]
    
    places = ["1st", "2nd", "3rd"]
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
            
            # Draw a crown on top of the 1st place avatar's head
            if i == 0:
                crown_color = (255, 215, 0)  # Bright gold
                cx = centers[i]
                cy = paste_y + 30  # Sits on top of the avatar head
                cw = 60  # Crown half-width
                ch = 40  # Crown height
                # Crown shape: 5-point zigzag polygon
                crown_points = [
                    (cx - cw, cy),
                    (cx - cw, cy - ch * 0.5),
                    (cx - cw * 0.5, cy - ch * 0.2),
                    (cx, cy - ch),
                    (cx + cw * 0.5, cy - ch * 0.2),
                    (cx + cw, cy - ch * 0.5),
                    (cx + cw, cy),
                ]
                draw.polygon(crown_points, fill=crown_color, outline=(180, 150, 0))
            
        # Draw placement on the podium block
        text_y = avatar_y_offsets[i] + 40
        place = places[i]
        
        # Draw shadow for readability
        draw.text((centers[i]+2, text_y+2), place, fill="black", anchor="ma", font=FONT_PLACE)
        draw.text((centers[i], text_y), place, fill="white", anchor="ma", font=FONT_PLACE)
            
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

async def get_podium_image(tier_name: str, top_3: List[Tuple[int, str, str, str]]) -> str:
    """
    Returns the path to the cached podium image.
    top_3 is a list of tuples: (user_id, avatar_url, display_name, username) ordered 1st, 2nd, 3rd.
    If a spot is empty, the tuple can be (0, "", "", "")
    """
    # Create cache key
    hash_str = f"{tier_name}"
    for uid, url, display_name, *rest in top_3:
        hash_str += f"_{uid}_{url}_{display_name}"
        
    cache_key = hashlib.md5(hash_str.encode()).hexdigest()
    file_path = os.path.join(CACHE_DIR, f"podium_{tier_name}_{cache_key}.png")
    
    if os.path.exists(file_path):
        return file_path
        
    # Not in cache, generate it
    async with aiohttp.ClientSession() as session:
        avatars = []
        names = []
        for uid, url, display_name, *rest in top_3:
            avatars.append(await download_avatar(session, url) if url else None)
            names.append(display_name)
            
    loop = asyncio.get_running_loop()
    buffer = await loop.run_in_executor(None, draw_simple_podium, tier_name, avatars, names)
    
    with open(file_path, "wb") as f:
        f.write(buffer.getbuffer())
        
    return file_path
