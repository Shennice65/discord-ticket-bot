import asyncio
import os
import sys
import discord

# Ensure we can import config and database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from database import Database

TARGET_PLAYERS = [
    {"display": "2u6n", "handle": "2u6n"},
    {"display": "sh3n", "handle": "rvining_"},
    {"display": "raphihii", "handle": "rapherin"},
    {"display": "lal", "handle": "lal._lal"},
    {"display": "asapad", "handle": "asapadz"},
    {"display": "kobalt", "handle": "kinetickobalt"},
    {"display": "masterial", "handle": "bombedmastery"},
    {"display": "KING", "handle": "hayden00657"}
]

class ElitesPopulator(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)
        self.db = Database()
        
    async def on_ready(self):
        print(f"Logged in as {self.user}!")
        if not await self.db.init():
            print("Failed to init database")
            await self.close()
            return
            
        guild = self.get_guild(Config.GUILD_ID)
        if not guild:
            print(f"Could not find guild with ID {Config.GUILD_ID}")
            await self.close()
            return
            
        print(f"Scanning {guild.member_count} members in {guild.name}...")
        
        found_users = []
        
        for target in TARGET_PLAYERS:
            matched_member = None
            for member in guild.members:
                if member.name.lower() == target["handle"].lower():
                    matched_member = member
                    break
                    
            if not matched_member:
                for member in guild.members:
                    if target["display"].lower() in member.display_name.lower() or member.name.lower() == target["display"].lower():
                        matched_member = member
                        break
                        
            if matched_member:
                print(f"[OK] Found {target['handle']}: {matched_member.name} ({matched_member.id})")
                found_users.append(matched_member)
            else:
                print(f"[FAIL] Could NOT find {target['handle']} / {target['display']}")
                
        print("\nUpdating Database...")
        dummy_start_id = 900000000000000000
        for i, target in enumerate(TARGET_PLAYERS):
            rank_num = i + 1
            rank_str = f"Elites {rank_num}"
            
            # Find if they were in found_users
            user_id = dummy_start_id + i
            user_name = target['handle']
            
            for user in found_users:
                if user.name.lower() == target['handle'].lower() or target['display'].lower() in user.display_name.lower():
                    user_id = user.id
                    user_name = user.name
                    break
                    
            print(f"Setting {user_name} (ID: {user_id}) to {rank_str}")
            await self.db.force_set_player_rank(user_id, rank_str, bypass_unrank=True)
            
        print("\nDone! Closing bot connection.")
        await self.close()

if __name__ == "__main__":
    if not Config.TOKEN:
        print("Error: DISCORD_TOKEN is missing!")
        sys.exit(1)
        
    client = ElitesPopulator()
    client.run(Config.TOKEN)
