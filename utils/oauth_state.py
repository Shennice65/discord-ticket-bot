"""Signed, expiring OAuth `state` that binds a Roblox login to the Discord user who asked for it.

Kept byte-identical in clips/oauth_state.py (the bot signs, the clips service verifies); the two
services deploy separately, so change both together.
"""
import hashlib
import hmac
import time
from typing import Optional

STATE_TTL_SECONDS = 600


def _mac(discord_id: int, expires: int, secret: str) -> str:
    message = f"{discord_id}.{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:32]


def sign_state(discord_id: int, secret: str, now: Optional[float] = None, ttl: int = STATE_TTL_SECONDS) -> str:
    if not secret:
        raise ValueError("OAuth state secret is not configured")
    expires = int((now if now is not None else time.time()) + ttl)
    return f"{int(discord_id)}.{expires}.{_mac(int(discord_id), expires, secret)}"


def verify_state(state: str, secret: str, now: Optional[float] = None) -> Optional[int]:
    """Return the Discord ID if the state is authentic and unexpired, else None."""
    if not secret or not state or len(state) > 100:
        return None
    parts = state.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    discord_id, expires = int(parts[0]), int(parts[1])
    if (now if now is not None else time.time()) > expires:
        return None
    if not hmac.compare_digest(parts[2], _mac(discord_id, expires, secret)):
        return None
    return discord_id


def resolve_state_secret(environ, config_doc) -> str:
    """The signing secret: environment or Mongo config doc, dedicated name first, webhook secret as fallback.

    Both services must resolve it the same way, so this lives next to sign/verify.
    """
    config_doc = config_doc or {}
    for name in ("ROBLOX_OAUTH_STATE_SECRET", "ROBLOX_WEBHOOK_SECRET"):
        value = environ.get(name) or config_doc.get(name)
        if value:
            return str(value)
    return ""
