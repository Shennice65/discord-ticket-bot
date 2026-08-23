import asyncio
import hashlib
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "database" / "betting.py"
SPEC = importlib.util.spec_from_file_location("betting_database_module", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
BettingMixin = MODULE.BettingMixin


class FakeLoginTokens:
    def __init__(self):
        self.updated_filter = None
        self.updated_value = None
        self.inserted = None

    async def update_many(self, query, update):
        self.updated_filter = query
        self.updated_value = update

    async def insert_one(self, document):
        self.inserted = document


def test_login_token_is_random_hashed_and_short_lived():
    owner = BettingMixin()
    owner.web_login_tokens = FakeLoginTokens()

    token = asyncio.run(owner.create_web_login_token(
        discord_user_id=123,
        discord_username="tester",
        display_name="Test User",
        avatar_url=None,
    ))

    assert len(token) == 43
    assert token not in repr(owner.web_login_tokens.inserted)
    assert owner.web_login_tokens.inserted["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    lifetime = owner.web_login_tokens.inserted["expires_at"] - owner.web_login_tokens.inserted["created_at"]
    assert lifetime.total_seconds() == 300
    assert owner.web_login_tokens.updated_filter == {"discord_user_id": 123, "used_at": None}
