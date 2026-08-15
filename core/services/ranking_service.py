import discord
import re
from config import Config
from typing import List, Optional, Tuple
from utils.ranking_utils import parse_rank
from utils.podium_generator import get_podium_image

TIERS = ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]

class RankingService:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db

    async def get_roblox_username(self, user_id: int) -> Optional[str]:
        # Check cache in DB first so we don't spam Bloxlink API
        cached = await self.db.db.roblox_usernames.find_one({"_id": user_id})
        if cached:
            return cached.get("username")

        # Get API Key from config collection
        config_doc = await self.db.db.config.find_one({"_id": "api_keys"})
        if not config_doc:
            return None
        bloxlink_key = config_doc.get("bloxlink_key")
        if not bloxlink_key:
            return None

        # Fetch from Bloxlink API
        import aiohttp
        
        guild_id = config_doc.get("guild_id_key")
        url = f"https://api.blox.link/v4/public/guilds/{guild_id}/discord-to-roblox/{user_id}"
        headers = {"Authorization": bloxlink_key}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        roblox_id = data.get("robloxID")
                        if roblox_id:
                            # Now ask Roblox for their actual username
                            roblox_url = f"https://users.roblox.com/v1/users/{roblox_id}"
                            async with session.get(roblox_url) as r_resp:
                                if r_resp.status == 200:
                                    r_data = await r_resp.json()
                                    roblox_name = r_data.get("name")
                                    if roblox_name:
                                        # Cache the username in MongoDB
                                        await self.db.db.roblox_usernames.update_one(
                                            {"_id": user_id},
                                            {"$set": {"username": roblox_name}},
                                            upsert=True
                                        )
                                        return roblox_name
                    else:
                        print(f"Bloxlink API returned status {resp.status} for user {user_id}")
        except Exception as e:
            print(f"Error fetching Bloxlink data for {user_id}: {e}")
        return None

    async def generate_leaderboard_content(self, page_index: int) -> Tuple[List[discord.Embed], Optional[discord.File]]:
        tier_name = TIERS[page_index]
        display_tier = "Novices" if tier_name == "Novice" else tier_name
        all_ranks = await self.db.get_all_player_ranks()
        
        tier_players = []
        for r in all_ranks:
            parsed = parse_rank(r.get('rank', ''))
            if parsed and parsed[0] == tier_name:
                streak = r.get('win_streak', 0)
                tier_players.append((r['user_id'], parsed[1], streak))
                
        tier_players.sort(key=lambda x: x[1])
        
        desc = f"# 🏆 {display_tier} Leaderboard\n\n"
        file = None
        
        config_doc = await self.db.db.config.find_one({"_id": "api_keys"})
        role_emojis = config_doc.get("role_emojis", {}) if config_doc else {}
        
        def get_role_emoji(member):
            if not member or not role_emojis:
                return ""
            for role in reversed(member.roles):
                if str(role.id) in role_emojis:
                    return role_emojis[str(role.id)]
            return ""
        
        if not tier_players:
            desc += "No players in this rank yet.\n"
        else:
            top_3 = []
            for i in range(min(3, len(tier_players))):
                uid = tier_players[i][0]
                user = self.bot.get_user(uid)
                if not user:
                    try:
                        user = await self.bot.fetch_user(uid)
                    except Exception:
                        pass
                
                avatar_url = user.display_avatar.url if user else ""
                
                discord_username = user.name if user else f"Player {uid}"
                display_name = discord_username
                username = discord_username
                top_3.append((uid, avatar_url, display_name, username))
                
            while len(top_3) < 3:
                top_3.append((0, "", "", ""))
                
            podium_path = await get_podium_image(display_tier, top_3)
            file = discord.File(podium_path, filename="podium.png")
            
            name_cache = {t[0]: (t[2], t[3]) for t in top_3 if t[0] != 0}
            for i, (uid, num, streak) in enumerate(tier_players[:3]):
                display_name, username = name_cache.get(uid, ("Unknown User", "Unknown User"))
                
                member = None
                for guild in self.bot.guilds:
                    member = guild.get_member(uid)
                    if member:
                        break
                        
                role_emoji_str = get_role_emoji(member)
                safe_display = discord.utils.escape_markdown(display_name)
                if display_name.lower() == username.lower():
                    name_text = f"**{safe_display}**"
                else:
                    name_text = f"**{safe_display}** `@{username}`"
                
                role_part = f"  {role_emoji_str}" if role_emoji_str else ""
                streak_text = f"  <:streak:1538257784137580655> {streak}" if streak >= 2 else ""
                
                desc += f"`#{i+1}` {name_text}{role_part}{streak_text}\n"
                
            if len(tier_players) > 3:
                desc += "\n**Runners Up**\n"
                for i, (uid, num, streak) in enumerate(tier_players[3:], 4):
                    # Cache lookup only to prevent gateway rate limits on deep pagination
                    member = None
                    for guild in self.bot.guilds:
                        member = guild.get_member(uid)
                        if member:
                            break
                    discord_username = member.name if member else "Unknown User"

                    display_name = discord_username
                    username = discord_username
                    
                    role_emoji_str = get_role_emoji(member)
                    safe_display = discord.utils.escape_markdown(display_name)
                    if display_name.lower() == username.lower():
                        name_text = f"{safe_display}"
                    else:
                        name_text = f"{safe_display} `@{username}`"
                        
                    role_part = f"  {role_emoji_str}" if role_emoji_str else ""
                    streak_text = f"  <:streak:1538257784137580655> {streak}" if streak >= 2 else ""
                    
                    desc += f"`#{i}` {name_text}{role_part}{streak_text}\n"
                
        desc += f"\n*Page {page_index + 1} of {len(TIERS)}*"
        
        embeds = []
        if file:
            image_embed = discord.Embed(color=discord.Color(0x2b2d31))
            image_embed.set_image(url="attachment://podium.png")
            embeds.append(image_embed)
            
        text_embed = discord.Embed(description=desc, color=discord.Color(0x2b2d31))
        embeds.append(text_embed)
            
        return embeds, file

    async def get_player_rank(self, user_id: int) -> Optional[str]:
        return await self.db.get_player_rank(user_id)
