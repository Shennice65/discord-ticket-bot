import re

APP_PY_PATH = 'clips/app.py'

def read_lines():
    with open(APP_PY_PATH, 'r', encoding='utf-8') as f:
        return f.readlines()

def write_lines(lines):
    with open(APP_PY_PATH, 'w', encoding='utf-8') as f:
        f.writelines(lines)

def get_function_block(lines, start_line_idx):
    end_idx = start_line_idx
    while end_idx < len(lines) and not lines[end_idx].startswith('def '):
        end_idx += 1
    end_idx += 1
    while end_idx < len(lines):
        line = lines[end_idx]
        if line.strip() == '' or line.strip().startswith('#'):
            end_idx += 1
            continue
        if not line.startswith(' ') and not line.startswith('\t'):
            break
        end_idx += 1
    return end_idx

def extract_routes(patterns, replacement_decorator):
    lines = read_lines()
    extracted_blocks = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        matched = False
        if line.startswith('@app.route'):
            for p in patterns:
                if p in line:
                    matched = True
                    break
        
        if matched:
            start_idx = i
            end_idx = get_function_block(lines, start_idx)
            block = lines[start_idx:end_idx]
            block[0] = block[0].replace('@app.route', replacement_decorator)
            extracted_blocks.extend(block)
            del lines[start_idx:end_idx]
            continue
            
        i += 1
    
    write_lines(lines)
    return ''.join(extracted_blocks)

if __name__ == '__main__':
    tournaments_patterns = [
        '"/api/tournament/standings"',
        '"/api/admin/tournament/adopt-local"',
        '"/api/admin/tournament/generate-playoffs"',
        '"/api/matches/recent"'
    ]
    code = extract_routes(tournaments_patterns, '@tournaments_bp.route')
    
    with open('clips/api/tournaments.py', 'w', encoding='utf-8') as f:
        f.write('''from flask import Blueprint, request, jsonify, current_app as app

from db_deps import db
from api.helpers import (
    require_web_session, is_betting_admin, current_web_session, utc_now
)
from tournament_engine import (
    calculate_standings, infer_round_robin_groups, generate_playoff_bracket
)

tournaments_bp = Blueprint('tournaments', __name__)

''')
        f.write(code)
    
    print("Extracted tournament routes!")
