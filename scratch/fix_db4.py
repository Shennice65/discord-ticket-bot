"""Update playoff bracket: record semifinal results and set up grand final."""
import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
client = MongoClient(os.getenv('MONGO_URI'))
db = client[os.getenv('MONGO_DB_NAME', 'discord_bot_db')]

FILTER = {'source': 'local'}

# --- Dump current state for verification ---
for mid in ('semi_1', 'semi_2', 'final'):
    doc = db.betting_matches.find_one({**FILTER, 'challonge_match_id': mid})
    if doc:
        print(f"[BEFORE] {mid}: team1={doc.get('team1_name')} (p1={doc.get('player1_id')}), "
              f"team2={doc.get('team2_name')} (p2={doc.get('player2_id')}), "
              f"state={doc.get('state')}, score={doc.get('score1')}-{doc.get('score2')}, "
              f"winner={doc.get('winner_id')}")
    else:
        print(f"[BEFORE] {mid}: NOT FOUND")

# 1. Semi 1: Luminosity (player2, id 300960621) beat Merleura 2-1
r1 = db.betting_matches.update_one(
    {**FILTER, 'challonge_match_id': 'semi_1'},
    {'$set': {
        'state': 'completed',
        'score1': 1,           # Merleura
        'score2': 2,           # Luminosity
        'winner_id': '300960621',  # Luminosity
    }}
)
print(f"\nsemi_1 update: matched={r1.matched_count}, modified={r1.modified_count}")

# 2. Semi 2: Toaster22436 (player2, id 300960587) beat Senpai 2-0
r2 = db.betting_matches.update_one(
    {**FILTER, 'challonge_match_id': 'semi_2'},
    {'$set': {
        'state': 'completed',
        'score1': 0,           # Senpai
        'score2': 2,           # Toaster22436
        'winner_id': '300960587',  # Toaster22436
    }}
)
print(f"semi_2 update: matched={r2.matched_count}, modified={r2.modified_count}")

# 3. Grand Final: Luminosity vs Toaster22436
r3 = db.betting_matches.update_one(
    {**FILTER, 'challonge_match_id': 'final'},
    {'$set': {
        'player1_id': '300960621',
        'team1_name': 'Luminosity',
        'player2_id': '300960587',
        'team2_name': 'Toaster22436',
        'state': 'upcoming',
    }}
)
print(f"final update: matched={r3.matched_count}, modified={r3.modified_count}")

# --- Dump updated state ---
print("\n--- AFTER ---")
for mid in ('semi_1', 'semi_2', 'final'):
    doc = db.betting_matches.find_one({**FILTER, 'challonge_match_id': mid})
    if doc:
        print(f"[AFTER] {mid}: team1={doc.get('team1_name')} (p1={doc.get('player1_id')}), "
              f"team2={doc.get('team2_name')} (p2={doc.get('player2_id')}), "
              f"state={doc.get('state')}, score={doc.get('score1')}-{doc.get('score2')}, "
              f"winner={doc.get('winner_id')}")

client.close()
print("\nDone!")
