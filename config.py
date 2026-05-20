import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TOKEN = os.getenv('DISCORD_TOKEN')
    GUILD_ID = int(os.getenv('GUILD_ID', 0))
    OBSERVER_ROLE_ID = int(os.getenv('OBSERVER_ROLE_ID', 0))
    LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', 0))
    TICKET_CATEGORY_ID = int(os.getenv('TICKET_CATEGORY_ID', 0))
    TURSO_DATABASE_URL = os.getenv('TURSO_DATABASE_URL')
    TURSO_AUTH_TOKEN = os.getenv('TURSO_AUTH_TOKEN')