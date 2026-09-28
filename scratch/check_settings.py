import os
from dotenv import load_dotenv
import certifi
from pymongo import MongoClient

load_dotenv('d:/main/coding/discord-bot/.env')
mongo_uri = os.environ.get('MONGO_URI')
client = MongoClient(mongo_uri, tlsCAFile=certifi.where())
db = client[os.environ.get('MONGO_DB_NAME', 'discord_bot_db')]

channel = db.bot_settings.find_one({"key": "roblox_stats_channel"})
print("Channel:", channel)

msg = db.bot_settings.find_one({"key": "roblox_stats_message"})
print("Message:", msg)
