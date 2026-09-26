import pytest
from unittest.mock import AsyncMock, MagicMock
from cogs.engagement_cmds import EngagementCommands
import discord

@pytest.mark.asyncio
async def test_vibecheck_command_registered():
    bot = MagicMock()
    cog = EngagementCommands(bot)
    
    # Verify command is in the cog's app_commands
    commands = cog.get_app_commands()
    assert len(commands) == 1
    assert commands[0].name == "vibecheck"
