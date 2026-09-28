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
    media_patterns = [
        '"/upload"',
        '"/import_url"',
        '"/api/progress/<task_id>"',
        '"/api/delete/<filename>"',
        '"/file/<filename>"',
        '"/clip/<clip_id>"',
        '"/api/clip/<clip_id>"',
        '"/api/clips"',
        '"/api/clips/<path:filename>/reaction"',
        '"/photo/<filename>"',
        '"/api/clip/<filename>/edit"'
    ]
    code = extract_routes(media_patterns, '@media_bp.route')
    
    with open('clips/api/media.py', 'w', encoding='utf-8') as f:
        f.write('''from flask import Blueprint, request, jsonify, redirect, send_file, current_app as app
import os
import uuid
import time
import hashlib
from werkzeug.utils import secure_filename
from concurrent.futures import ThreadPoolExecutor

from db_deps import db, redis_connection
from api.helpers import (
    require_web_session, require_clips_admin_session,
    rate_limit, utc_now, current_web_session
)
from r2_client import (
    upload_to_r2, delete_from_r2, r2_is_configured
)
from video import process_video, write_first_frame
from content_moderation import scan_video_with_hive
from security import validate_public_import_url

media_bp = Blueprint('media', __name__)

# TASKS dictionary needs to be imported or recreated. Let's re-create it here if app.py used it just for these routes.
TASKS = {}
executor = ThreadPoolExecutor(max_workers=3)

''')
        f.write(code)
    
    # Remove TASKS and executor from app.py
    lines = read_lines()
    lines = [l for l in lines if not l.startswith('TASKS: Dict[str, Any] = {}')]
    lines = [l for l in lines if not l.startswith('executor = ThreadPoolExecutor')]
    write_lines(lines)

    print("Extracted media routes!")
