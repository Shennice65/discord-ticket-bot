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
    end_idx = start_line_idx + 1
    while end_idx < len(lines):
        line = lines[end_idx]
        if line.strip() == '' or line.strip().startswith('#'):
            end_idx += 1
            continue
        if not line.startswith(' ') and not line.startswith('\t'):
            break
        end_idx += 1
    return end_idx

def extract_funcs(func_names):
    lines = read_lines()
    extracted = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        matched = False
        for fn in func_names:
            if line.startswith(f'def {fn}('):
                matched = True
                break
        
        if matched:
            end_idx = get_function_block(lines, i)
            extracted.extend(lines[i:end_idx])
            del lines[i:end_idx]
            continue
            
        i += 1
    
    write_lines(lines)
    return ''.join(extracted)

if __name__ == '__main__':
    funcs = [
        'utc_now',
        'serialize_datetime',
        'current_web_session',
        'public_web_user',
        'betting_admin_ids',
        'is_betting_admin',
        'clips_admin_session_valid',
        'admin_password_valid',
        'require_web_session',
        'require_clips_admin_session',
    ]
    code = extract_funcs(funcs)
    
    with open('clips/api/helpers.py', 'w', encoding='utf-8') as f:
        f.write('''import os
import hmac
from datetime import datetime, timezone
from flask import request
from functools import wraps
from db_deps import db
from security import hash_secret

# Constants extracted from app.py
LOGIN_CONFIRM_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<title>Confirm Login</title>
</head>
<body>
    <h2>Sign in to ATL Clips</h2>
    <p>Signing in as: {{ login.discord_username }}</p>
    <form method="POST">
        <input type="hidden" name="token" value="{{ token }}" />
        <button type="submit" style="padding: 10px 20px; background: #5865F2; color: white; border: none; border-radius: 4px; font-size: 16px; cursor: pointer;">Confirm Login</button>
    </form>
</body>
</html>
"""
STARTING_COIN_BALANCE = 500
WEB_SESSION_DAYS = 30
CLIPS_ADMIN_SESSION_MINUTES = 60
CLIPS_ADMIN_PASSWORD_HASH = os.environ.get("CLIPS_ADMIN_PASSWORD_HASH")

''')
        f.write(code)
    
    print("Extracted helpers!")
