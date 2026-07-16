import asyncio
import os
import sys
import discord

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from config import Config

user_list = [
    "Lchh", "Chiz", "KayW621", "vehlra", "RandomNoodles7", "Bumblebeeshen",
    "Zntrix", "KennLovesTracking", "Whi4e6", "DeadEye_Hunter2", "Tennyson",
    "chat581", "bronx boy hnobbko", "iiOmq_ZilxX", "Caprice", "itemz",
    "SigmaCroc", "justwannaplay", "sleepymilk", "RAXGAMEROG", "not_theaa0",
    "wibuk09", "Rabbid", "ClydeAdrienne05"
]

class NoviceChecker(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)
        self.db = Database()
        
    async def on_ready(self):
        print(f"Logged in as {self.user}!")
        if not await self.db.init():
            return
            
        guild = self.get_guild(Config.GUILD_ID)
        
        for name in user_list:
            found = False
            for member in guild.members:
                if name.lower() in member.name.lower() or name.lower() in member.display_name.lower():
                    player = await self.db.player_ranks.find_one({"user_id": member.id})
                    rank = player.get("rank", "Unranked") if player else "Unranked"
                    print(f"User {name} -> {member.name} ({member.id}) is currently {rank}")
                    found = True
                    break
            if not found:
                print(f"User {name} NOT FOUND in guild.")
                
        await self.close()

if __name__ == "__main__":
    client = NoviceChecker()
    client.run(Config.TOKEN)
