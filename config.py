import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TOKEN = os.environ.get('DISCORD_TOKEN') or os.getenv('DISCORD_TOKEN')
    GUILD_ID = int(os.environ.get('GUILD_ID', 0))
    AI_PROVIDER = os.environ.get('AI_PROVIDER', 'deepseek').strip().casefold()
    _raw_gemini_keys = os.environ.get('GEMINI_API_KEY', '') or os.getenv('GEMINI_API_KEY', '')
    GEMINI_API_KEYS = [key.strip() for key in _raw_gemini_keys.split(',') if key.strip()]
    GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite')
    GEMINI_FALLBACK_MODELS = [
        model.strip() for model in os.environ.get(
            'GEMINI_FALLBACK_MODELS', 'gemini-3.6-flash,gemini-3.8-flash'
        ).split(',') if model.strip()
    ]
    GEMINI_EMBEDDING_MODEL = os.environ.get('GEMINI_EMBEDDING_MODEL', 'gemini-embedding-001')
    GEMINI_EMBEDDING_DIMENSIONS = int(os.environ.get('GEMINI_EMBEDDING_DIMENSIONS', '256'))
    _raw_deepseek_keys = os.environ.get('DEEPSEEK_API_KEY', '') or os.getenv('DEEPSEEK_API_KEY', '')
    DEEPSEEK_API_KEYS = [key.strip() for key in _raw_deepseek_keys.split(',') if key.strip()]
    DEEPSEEK_MODEL = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
    DEEPSEEK_FALLBACK_MODELS = [
        model.strip() for model in os.environ.get('DEEPSEEK_FALLBACK_MODELS', '').split(',')
        if model.strip()
    ]
    AI_REQUEST_TIMEOUT_SECONDS = max(30, int(os.environ.get('AI_REQUEST_TIMEOUT_SECONDS', '180')))
    AI_MAX_HISTORY_MESSAGES = max(1, int(os.environ.get('AI_MAX_HISTORY_MESSAGES', '6')))
    AI_MAX_OUTPUT_TOKENS = max(100, min(int(os.environ.get('AI_MAX_OUTPUT_TOKENS', '600')), 1200))
    AI_QUOTA_TOKEN_LIMIT = max(500, int(os.environ.get('AI_QUOTA_TOKEN_LIMIT', '5000')))
    AI_QUOTA_WINDOW_SECONDS = max(60, int(os.environ.get('AI_QUOTA_WINDOW_SECONDS', '360')))
    OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '') or os.getenv('OPENROUTER_API_KEY', '')
    OPENROUTER_MODEL = os.environ.get('OPENROUTER_MODEL', 'google/gemini-3-flash-preview')
    OPENROUTER_FALLBACK_MODELS = [
        model.strip() for model in os.environ.get(
            'OPENROUTER_FALLBACK_MODELS',
            'anthropic/claude-sonnet-4.5,openai/gpt-5.1',
        ).split(',') if model.strip()
    ]
    OPENROUTER_EMBEDDING_MODEL = os.environ.get(
        'OPENROUTER_EMBEDDING_MODEL', 'openai/text-embedding-3-small'
    )
    OPENROUTER_EMBEDDING_DIMENSIONS = int(os.environ.get('OPENROUTER_EMBEDDING_DIMENSIONS', '256'))
    AGENT_SIDECAR_ENABLED = os.environ.get('AGENT_SIDECAR_ENABLED', 'true').casefold() in {
        '1', 'true', 'yes', 'on'
    }
    AGENT_SIDECAR_HOST = os.environ.get('AGENT_SIDECAR_HOST', '127.0.0.1')
    AGENT_SIDECAR_PORT = int(os.environ.get('AGENT_SIDECAR_PORT', '8765'))
    AGENT_MAX_STEPS = max(1, min(int(os.environ.get('AGENT_MAX_STEPS', '4')), 4))
    AGENT_APPROVAL_TOOLS = [
        tool.strip() for tool in os.environ.get('AGENT_APPROVAL_TOOLS', '').split(',')
        if tool.strip()
    ]
    MASTER_ADMIN_ID = 442188857014747136
    OBSERVER_ROLE_ID = int(os.environ.get('OBSERVER_ROLE_ID', 0))
    LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', 0))
    TRIAL_OBSERVER_ROLE_ID = int(os.environ.get('TRIAL_OBSERVER_ROLE_ID', 0))
    NO_PERSONAL_OBS_ROLE_ID = int(os.environ.get('NO_PERSONAL_OBS_ROLE_ID', 0))
    RANK_LOG_CHANNEL_ID = int(os.environ.get('RANK_LOG_CHANNEL_ID', 0))
    RANKING_PANEL_CHANNEL_ID = int(os.environ.get('RANKING_PANEL_CHANNEL_ID', 0))
    # Fallback only; the live value is read from MongoDB config when available.
    AI_MEMORY_CHANNEL_ID = int(os.environ.get('AI_MEMORY_CHANNEL_ID', 0))
    TICKET_CATEGORY_ID = int(os.environ.get('TICKET_CATEGORY_ID', 0))
    PHANTOM_ROLE_ID = int(os.environ.get('PHANTOM_ROLE_ID', 0))
    CHAMPION_ROLE_ID = int(os.environ.get('CHAMPION_ROLE_ID', 0))
    ELITE_ROLE_ID = int(os.environ.get('ELITE_ROLE_ID', 0))
    LEGEND_ROLE_ID = int(os.environ.get('LEGEND_ROLE_ID', 0))
    MASTERS_ROLE_ID = int(os.environ.get('MASTERS_ROLE_ID', 0))
    NOVICE_ROLE_ID = int(os.environ.get('NOVICE_ROLE_ID', 0))
    MONGO_URI = os.environ.get('MONGO_URI') or os.getenv('MONGO_URI')
    CO_OWNER_ROLE_ID = int(os.environ.get('CO_OWNER_ROLE_ID', 0))
    SHEN_ID = int(os.environ.get('SHEN_ID', 0))
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
    MEMBER_ROLE_ID = int(os.environ.get('MEMBER_ROLE_ID', 0))
    VERSION = "1.13.3"
