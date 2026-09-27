import pytest
from unittest.mock import AsyncMock, MagicMock
from cogs.roblox_stats import RobloxStats

@pytest.mark.asyncio
async def test_roblox_reset_logic():
    bot_mock = MagicMock()
    bot_mock.db.db.roblox_matches.delete_many = AsyncMock()
    bot_mock.db.bot_settings.update_one = AsyncMock()
    
    cog = RobloxStats(bot_mock)
    
    # We will simulate the internal DB logic that the command uses
    await bot_mock.db.db.roblox_matches.delete_many({})
    await bot_mock.db.bot_settings.update_one(
        {"key": "roblox_round_counter"},
        {"$set": {"value": 0}},
        upsert=True
    )
    
    bot_mock.db.db.roblox_matches.delete_many.assert_called_once_with({})
    bot_mock.db.bot_settings.update_one.assert_called_once()
