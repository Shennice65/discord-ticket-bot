import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TOKEN = os.getenv('DISCORD_TOKEN')
    GUILD_ID = int(os.getenv('GUILD_ID', 0))  # Your server ID
    OBSERVER_ROLE_ID = int(os.getenv('OBSERVER_ROLE_ID', 0))  # Role ID for observers/moderators
    LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', 0))  # Channel for logging all tickets
    TICKET_CATEGORY_ID = int(os.getenv('TICKET_CATEGORY_ID', 0))  # Category where tickets are created
    DATABASE_PATH = 'tickets.db'