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
    roblox_patterns = [
        '"/api/players"',
        '"/api/avatars"',
        '"/api/player/<int:user_id>/history"',
        '"/api/roblox/live-update"',
        '"/api/roblox/match-stats"'
    ]
    code_roblox = extract_routes(roblox_patterns, '@roblox_bp.route')
    
    with open('clips/api/roblox.py', 'w', encoding='utf-8') as f:
        f.write('''from flask import Blueprint, request, jsonify, current_app as app
import time

from db_deps import db
from api.helpers import rate_limit, utc_now
from player_identity import build_player_identity

roblox_bp = Blueprint('roblox', __name__)

''')
        f.write(code_roblox)
        
    proxy_patterns = [
        '"/ingest/<path:proxy_path>"'
    ]
    code_proxy = extract_routes(proxy_patterns, '@proxy_bp.route')
    
    with open('clips/api/proxy.py', 'w', encoding='utf-8') as f:
        f.write('''from flask import Blueprint, request, jsonify, Response, current_app as app
import requests as http_requests

proxy_bp = Blueprint('proxy', __name__)

POSTHOG_INGEST_HOST = "https://eu.i.posthog.com"
POSTHOG_ASSET_HOST = "https://eu-assets.i.posthog.com"
POSTHOG_PROXY_RESPONSE_HEADERS = {"content-type", "cache-control", "etag", "last-modified", "vary"}

''')
        f.write(code_proxy)
    
    # Remove posthog constants from app.py
    lines = read_lines()
    lines = [l for l in lines if not l.startswith('POSTHOG_')]
    write_lines(lines)

    print("Extracted roblox and proxy routes!")
