import os
import re

APP_PY_PATH = 'clips/app.py'

def read_lines():
    with open(APP_PY_PATH, 'r', encoding='utf-8') as f:
        return f.readlines()

def write_lines(lines):
    with open(APP_PY_PATH, 'w', encoding='utf-8') as f:
        f.writelines(lines)

def get_function_block(lines, start_line_idx):
    # Extracts the route decorator and the function below it
    end_idx = start_line_idx
    # Find the def
    while end_idx < len(lines) and not lines[end_idx].startswith('def '):
        end_idx += 1
    # Find the end of the function (next unindented line that is not a comment/empty)
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

def extract_routes(patterns, replacement_decorator='@blueprint.route'):
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
            
            # replace decorator
            block[0] = block[0].replace('@app.route', replacement_decorator)
            extracted_blocks.extend(block)
            
            # remove from original
            del lines[start_idx:end_idx]
            continue
            
        i += 1
    
    write_lines(lines)
    return ''.join(extracted_blocks)

if __name__ == '__main__':
    # 1. Auth routes
    auth_patterns = [
        '"/auth/redeem"',
        '"/api/auth/me"',
        '"/api/auth/logout"',
        '"/checkpassword"',
        '"/api/clips/admin/session"'
    ]
    auth_code = extract_routes(auth_patterns, '@auth_bp.route')
    
    with open('clips/api/auth.py', 'w', encoding='utf-8') as f:
        f.write('''from flask import Blueprint, request, jsonify, session, redirect, current_app as app
from datetime import datetime, timedelta, timezone
from db_deps import db
# You will need to import current_web_session and other helpers here or in a separate file.

auth_bp = Blueprint('auth', __name__)

''')
        f.write(auth_code)
        
    print("Extracted auth routes!")
