import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
client = MongoClient(os.getenv('MONGO_URI'))
db = client[os.getenv('MONGO_DB_NAME', 'discord_bot_db')]

# 1. Update playin next_match_ids
db.betting_matches.update_one({'challonge_match_id': 'playin_1', 'source': 'local'}, {'$set': {'next_match_id': 'semi_1'}})
db.betting_matches.update_one({'challonge_match_id': 'playin_2', 'source': 'local'}, {'$set': {'next_match_id': 'semi_2'}})

# 2. Fix semi_1 (Merleura vs Luminosity)
db.betting_matches.update_one({'challonge_match_id': 'semi_1', 'source': 'local'}, {'$set': {'player2_id': '300960621', 'team2_name': 'Luminosity'}})

# 3. Fix semi_2 (Senpai vs Toaster22436)
db.betting_matches.update_one({'challonge_match_id': 'semi_2', 'source': 'local'}, {'$set': {'player2_id': '300960587', 'team2_name': 'Toaster22436'}})

print('Fixed database!')
