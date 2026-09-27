import os
from dotenv import load_dotenv
load_dotenv('d:/main/coding/discord-bot/.env')
from pymongo import MongoClient
import certifi

mongo_uri = os.environ.get('MONGO_URI')
client = MongoClient(mongo_uri, tlsCAFile=certifi.where())
db = client[os.environ.get('MONGO_DB_NAME', 'discord_bot_db')]

db.betting_matches.update_one({'challonge_match_id': 'semi_2'}, {'$set': {'player2_id': None, 'team2_name': 'TBD'}})
db.betting_matches.update_one({'challonge_match_id': 'semi_1'}, {'$set': {'player2_id': '300960621', 'team2_name': 'Luminosity'}})
print('Fixed DB routing')
