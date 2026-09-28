import re

with open('clips/api/auth.py', 'r') as f:
    text = f.read()

text = text.replace('from app import', 'from api.helpers import')

with open('clips/api/auth.py', 'w') as f:
    f.write(text)

with open('clips/app.py', 'r', encoding='utf-8') as f:
    app_text = f.read()

app_text = re.sub(r'LOGIN_CONFIRM_TEMPLATE = \"\"\"(?:.*?)\"\"\"\n', '', app_text, flags=re.DOTALL)
app_text = re.sub(r'STARTING_COIN_BALANCE = 500\n', '', app_text)
app_text = re.sub(r'WEB_SESSION_DAYS = 30\n', '', app_text)
app_text = re.sub(r'CLIPS_ADMIN_SESSION_MINUTES = 60\n', '', app_text)
app_text = re.sub(r'CLIPS_ADMIN_PASSWORD_HASH = os\.environ\.get\(\"CLIPS_ADMIN_PASSWORD_HASH\"\)\n', '', app_text)

if 'from api.helpers import' not in app_text:
    app_text = app_text.replace('from flask import Flask', 'from flask import Flask\nfrom api.helpers import utc_now, serialize_datetime, current_web_session, public_web_user, betting_admin_ids, is_betting_admin, admin_password_valid, clips_admin_session_valid, require_web_session, require_clips_admin_session\n')

with open('clips/app.py', 'w', encoding='utf-8') as f:
    f.write(app_text)
