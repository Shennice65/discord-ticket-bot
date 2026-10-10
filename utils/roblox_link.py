import os
from typing import Optional

from config import Config
from utils.oauth_state import resolve_state_secret, sign_state


async def build_login_link(db, discord_id: int) -> Optional[str]:
    """The Roblox OAuth login link for one Discord user, or None if no signing secret is configured.

    `db` is the bot's database wrapper (bot.db). The state is signed and expires, so the link only works for
    the person it was issued to.
    """
    config_doc = await db.db.config.find_one({"_id": "api_keys"}) or {}
    secret = resolve_state_secret(os.environ, config_doc)
    if not secret:
        return None
    state = sign_state(discord_id, secret)
    return f"{Config.CLIPS_SERVICE_URL.rstrip('/')}/api/roblox/login?state={state}"
