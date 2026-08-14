import os
import re

with open('database.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find imports
imports = []
class_start = 0
for i, line in enumerate(lines):
    if line.startswith('class Database'):
        class_start = i
        break
    imports.append(line)

imports = "".join(imports)

# Find methods
methods = []
start = -1
name = ''
for i in range(class_start + 1, len(lines)):
    line = lines[i]
    if line.startswith('    def ') or line.startswith('    async def '):
        if start != -1:
            methods.append((name, "".join(lines[start:i])))
        start = i
        name = line.split('def ')[1].split('(')[0]
if start != -1:
    methods.append((name, "".join(lines[start:])))

# Categorize methods
categories = {
    'connection': ['__init__', 'init'],
    'settings': ['_next_id', 'get_setting', 'set_setting', 'log_undo_action', 'undo_last_action', 'get_ranking_config', 'set_ranking_config'],
    'ladder': ['get_all_player_ranks', 'get_tier_count', 'get_player_rank', 'get_player_by_rank', 'get_global_rank_index', 'update_player_rank', 'get_ranked_cooldown', 'update_ranked_cooldown', 'reset_ranked_cooldown', 'reset_ranked_cooldown_only', 'get_obs_cooldown', 'update_obs_cooldown', 'reset_obs_cooldown', '_bulk_reassign_ranks', 'unrank_player', '_get_unrank_cooldown_days', 'get_unrank_cooldown', 'is_player_self_unranked', 'can_player_r1', 'remove_player_from_ladder', 'force_set_player_rank', 'process_match_result', 'get_rematch_cooldown'],
    'tickets': ['create_ticket', 'create_ranked_ticket_db', 'close_ticket', 'get_ticket_by_channel', 'mark_ducking_ping_sent', 'add_ranked_result', 'add_observation_result'],
    'history': ['get_user_history', 'get_user_observation_count'],
    'admin': ['clear_unrank_penalty', 'reset_all_timers', 'clear_ranked_history', 'clear_observation_history', 'clear_all_history']
}

os.makedirs('database', exist_ok=True)

for cat, method_names in categories.items():
    cat_code = imports + f"\nclass {cat.capitalize()}Mixin:\n"
    found = False
    for name in method_names:
        for m in methods:
            if m[0] == name:
                cat_code += m[1]
                found = True
                break
    if found:
        with open(f'database/{cat}.py', 'w', encoding='utf-8') as f:
            f.write(cat_code)

init_code = imports + "\n"
for cat in categories.keys():
    init_code += f"from .{cat} import {cat.capitalize()}Mixin\n"

class_names = [f"{cat.capitalize()}Mixin" for cat in categories.keys()]
init_code += f"\nclass Database({', '.join(class_names)}):\n    def __init__(self):\n        super().__init__()\n"

with open('database/__init__.py', 'w', encoding='utf-8') as f:
    f.write(init_code)
