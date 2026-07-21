import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TOKEN = os.environ.get('DISCORD_TOKEN') or os.getenv('DISCORD_TOKEN')
    GUILD_ID = int(os.environ.get('GUILD_ID', 0))
    OBSERVER_ROLE_ID = int(os.environ.get('OBSERVER_ROLE_ID', 0))
    LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', 0))
    TRIAL_OBSERVER_ROLE_ID = int(os.environ.get('TRIAL_OBSERVER_ROLE_ID', 0))
    NO_PERSONAL_OBS_ROLE_ID = int(os.environ.get('NO_PERSONAL_OBS_ROLE_ID', 0))
    RANK_LOG_CHANNEL_ID = int(os.environ.get('RANK_LOG_CHANNEL_ID', 0))
    RANKING_PANEL_CHANNEL_ID = int(os.environ.get('RANKING_PANEL_CHANNEL_ID', 0))
    TICKET_CATEGORY_ID = int(os.environ.get('TICKET_CATEGORY_ID', 0))
    MONGO_URI = os.environ.get('MONGO_URI') or os.getenv('MONGO_URI')
    VERSION = "1.8.9"