from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class BettingMixin:
    async def create_web_login_token(
        self,
        *,
        discord_user_id: int,
        discord_username: str,
        display_name: str,
        avatar_url: str | None,
        ttl_minutes: int = 5,
    ) -> str:
        """Create a short-lived, single-use token for the betting website."""
        now = datetime.now(timezone.utc)
        raw_token = secrets.token_urlsafe(32)

        # Supersede older links without deleting audit data. A user should only
        # have one usable login link at a time.
        await self.web_login_tokens.update_many(
            {"discord_user_id": discord_user_id, "used_at": None},
            {"$set": {"used_at": now, "invalidated_reason": "superseded"}},
        )
        await self.web_login_tokens.insert_one(
            {
                "_id": str(uuid.uuid4()),
                "token_hash": _token_hash(raw_token),
                "discord_user_id": discord_user_id,
                "discord_username": discord_username,
                "display_name": display_name,
                "avatar_url": avatar_url,
                "created_at": now,
                "expires_at": now + timedelta(minutes=ttl_minutes),
                "used_at": None,
            }
        )
        return raw_token
