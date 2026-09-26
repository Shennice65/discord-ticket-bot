# Vibe Check Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a `/vibecheck` slash command that exposes the bot's secret AI engagement and style profiling to the user in a fun, gamified embed.

**Architecture:** A new `EngagementCommands` discord Cog will be created to keep the new command isolated from the massive `chat.py` file. It will use the existing `UserEngagementScorer` from `ai.engagement` to fetch the user's `StyleFingerprint` and `UserProfile`, formatting them into a stylistic Discord Embed. 

**Tech Stack:** discord.py (app_commands), pytest for testing, Python async/await

## Global Constraints

- Discord app_commands require the command to be synced or registered to the Bot's command tree.
- Match existing project styling (use `discord.Embed`, handle colors).
- Use `UserEngagementScorer` properly (it needs the bot instance).

---

### Task 1: Create the EngagementCommands Cog and Command

**Files:**
- Create: `cogs/engagement_cmds.py`
- Modify: `main.py:65-80`
- Test: `tests/test_engagement_cmds.py`

**Interfaces:**
- Consumes: `ai.engagement.UserEngagementScorer`, `discord.app_commands`
- Produces: `EngagementCommands` cog with `/vibecheck` command

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engagement_cmds.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'cogs.engagement_cmds'"

- [ ] **Step 3: Write minimal implementation**

Create `cogs/engagement_cmds.py`:
```python
import discord
from discord.ext import commands
from discord import app_commands
from ai.engagement import UserEngagementScorer

class EngagementCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scorer = UserEngagementScorer(bot)

    @app_commands.command(name="vibecheck", description="See what the AI thinks of your chat style!")
    async def vibecheck(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        fingerprint = await self.scorer._get_fingerprint(interaction.user.id)
        profile = await self.scorer.get_profile(interaction.user.id, interaction.user)
        
        embed = discord.Embed(
            title=f"Vibe Check: {interaction.user.display_name}",
            color=discord.Color.purple()
        )
        
        embed.add_field(name="Engagement Tier", value=profile.tier.capitalize(), inline=True)
        embed.add_field(name="Engagement Score", value=str(profile.score), inline=True)
        
        style_desc = []
        if fingerprint.caps_style == "lowercase":
            style_desc.append("• You type mostly in lowercase (too cool for the shift key).")
        elif fingerprint.caps_style == "uppercase":
            style_desc.append("• YOU YELL A LOT.")
        else:
            style_desc.append("• Normal capitalization.")
            
        if fingerprint.punctuation == "formal":
            style_desc.append("• You use periods. Very formal.")
        elif fingerprint.punctuation == "expressive":
            style_desc.append("• Very expressive punctuation!?!")
            
        length_map = {"short": "Keep it brief", "medium": "Average length", "long": "You write essays"}
        style_desc.append(f"• {length_map.get(fingerprint.avg_length, 'Average length')}.")
        
        if fingerprint.emoji_heavy:
            style_desc.append("• You love emojis 💯🔥")
            
        if fingerprint.slang_markers:
            style_desc.append(f"• Favorite slang: {', '.join(fingerprint.slang_markers)}")
            
        embed.add_field(name="AI's Notes on your Style", value="\n".join(style_desc) or "No distinct style detected yet.", inline=False)
        
        if profile.style_hint:
            embed.set_footer(text=f"AI Prompt modifier: {profile.style_hint}")
            
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(EngagementCommands(bot))
```

Update `main.py` around line 78 (after loading other cogs):
```python
        await self.load_extension("cogs.activity")
        await self.load_extension("cogs.engagement_cmds")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_engagement_cmds.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cogs/engagement_cmds.py main.py tests/test_engagement_cmds.py
git commit -m "feat: add vibecheck slash command for AI engagement profiling"
```
