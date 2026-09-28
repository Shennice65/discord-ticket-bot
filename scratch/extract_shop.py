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
    shop_patterns = [
        '"/api/profile-frames"',
        '"/api/profile-frames/purchase"',
        '"/api/profile-frames/equip"',
        '"/api/admin/profile-frames/refresh-daily"',
        '"/api/admin/profile-frames"',
        '"/api/admin/profile-frames/upload"',
        '"/api/admin/profile-frames/<frame_id>"',
        '"/api/admin/wallets/search"',
        '"/api/admin/wallets/adjust"'
    ]
    code = extract_routes(shop_patterns, '@shop_bp.route')
    
    with open('clips/api/shop.py', 'w', encoding='utf-8') as f:
        f.write('''from flask import Blueprint, request, jsonify, current_app as app
import uuid
import os
import time
from werkzeug.utils import secure_filename
from pymongo import ReturnDocument

from db_deps import db
from api.helpers import (
    require_web_session, require_clips_admin_session,
    rate_limit, utc_now, current_web_session
)
from profile_shop import (
    serialize_profile_frames as catalog_profile_frames,
    daily_profile_frame_offers, purchase_wallet_filter,
    purchase_wallet_update, validate_profile_frame_input,
    validate_decoration_upload, validate_wallet_adjustment,
    discord_id_candidates
)
from r2_client import r2_is_configured, upload_to_r2, delete_from_r2

shop_bp = Blueprint('shop', __name__)

''')
        f.write(code)
    
    print("Extracted shop routes!")
