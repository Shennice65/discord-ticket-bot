import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TOKEN = os.environ.get('DISCORD_TOKEN') or os.getenv('DISCORD_TOKEN')
    GUILD_ID = int(os.environ.get('GUILD_ID', 0))
    MASTER_ADMIN_ID = 442188857014747136
    OBSERVER_ROLE_ID = int(os.environ.get('OBSERVER_ROLE_ID', 0))
    LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', 0))
    TRIAL_OBSERVER_ROLE_ID = int(os.environ.get('TRIAL_OBSERVER_ROLE_ID', 0))
    NO_PERSONAL_OBS_ROLE_ID = int(os.environ.get('NO_PERSONAL_OBS_ROLE_ID', 0))
    RANK_LOG_CHANNEL_ID = int(os.environ.get('RANK_LOG_CHANNEL_ID', 0))
    RANKING_PANEL_CHANNEL_ID = int(os.environ.get('RANKING_PANEL_CHANNEL_ID', 0))
    TICKET_CATEGORY_ID = int(os.environ.get('TICKET_CATEGORY_ID', 0))
    PHANTOM_ROLE_ID = int(os.environ.get('PHANTOM_ROLE_ID', 0))
    CHAMPION_ROLE_ID = int(os.environ.get('CHAMPION_ROLE_ID', 0))
    ELITE_ROLE_ID = int(os.environ.get('ELITE_ROLE_ID', 0))
    LEGEND_ROLE_ID = int(os.environ.get('LEGEND_ROLE_ID', 0))
    MASTERS_ROLE_ID = int(os.environ.get('MASTERS_ROLE_ID', 0))
    NOVICE_ROLE_ID = int(os.environ.get('NOVICE_ROLE_ID', 0))
    MONGO_URI = os.environ.get('MONGO_URI') or os.getenv('MONGO_URI')
    CO_OWNER_ROLE_ID = int(os.environ.get('CO_OWNER_ROLE_ID', 0))
    MONGO_DB_NAME = os.environ.get('MONGO_DB_NAME', 'discord_bot_db')
    # The public leaderboard is not secret. Keep the production URL as a
    # fallback so ticket links cannot silently disappear when a deployment
    # environment is missing or has not reloaded this variable.
    CLIPS_SERVICE_URL = os.environ.get('CLIPS_SERVICE_URL', 'https://atlclips.site')
    CLIPS_ADMIN_PASSWORD = os.environ.get('CLIPS_ADMIN_PASSWORD', '')
    BETTING_SITE_URL = os.environ.get('BETTING_SITE_URL', CLIPS_SERVICE_URL).rstrip('/')
    BETTING_NOTIFICATION_CHANNEL_ID = os.environ.get('BETTING_NOTIFICATION_CHANNEL_ID', '')
    BETTING_NOTIFICATION_ROLE_ID = os.environ.get('BETTING_NOTIFICATION_ROLE_ID', '')
    WEB_LOGIN_MIN_ACCOUNT_AGE_DAYS = int(os.environ.get('WEB_LOGIN_MIN_ACCOUNT_AGE_DAYS', '30'))
    WEB_LOGIN_MIN_MEMBERSHIP_DAYS = int(os.environ.get('WEB_LOGIN_MIN_MEMBERSHIP_DAYS', '7'))
    VERSION = "1.13.3"
