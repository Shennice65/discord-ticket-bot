# Roblox Scoreboard Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Format the Discord match scoreboard into a clean, aligned monospaced table to improve readability.

**Architecture:** Modify `build_match_embed` in `cogs/roblox_stats.py` to generate an ASCII-style table using Discord's markdown code block feature (```), ensuring column alignment via string padding. Same applies for the live update format in `process_live_update`.

**Tech Stack:** Python, discord.py

## Global Constraints

- Do not change the database schema or how kills are tracked.
- Ensure the table fits within Discord embed limits (Description max length: 4096 characters).
- Column headers must be "Player", "K", "D", "A".

---

### Task 1: Refactor `build_match_embed`

**Files:**
- Modify: `d:\main\coding\discord-bot\cogs\roblox_stats.py`

**Interfaces:**
- Consumes: The `stats` array containing dictionaries with keys `Name`, `Kills`, `Deaths`, `Assists`.
- Produces: A formatted Discord Embed.

- [ ] **Step 1: Write the updated implementation for `build_match_embed`**

```python
    def build_match_embed(self, match: dict) -> discord.Embed:
        stats = match.get('stats', [])
        
        if not stats:
            description = "No stats recorded this round."
        else:
            # Find the longest name for column alignment (minimum width of 16)
            max_name_len = max(len(stat.get('Name', 'Unknown')) for stat in stats)
            name_width = max(max_name_len, 16)
            
            # Build table header
            description = "```\n"
            description += f"{'Player':<{name_width}} | K  | D  | A\n"
            description += "-" * (name_width + 17) + "\n"
            
            # Build table rows
            for stat in stats:
                name = stat.get('Name', 'Unknown')
                k = stat.get('Kills', 0)
                d = stat.get('Deaths', 0)
                a = stat.get('Assists', 0)
                description += f"{name:<{name_width}} | {k:<2} | {d:<2} | {a:<2}\n"
            
            description += "```"
            
        embed = discord.Embed(
            title=f"🏁 Match Concluded! (Round {match.get('round_num')})",
            description=description,
            color=0x00FF00,
            timestamp=match.get('timestamp') or discord.utils.utcnow()
        )
        return embed
```

- [ ] **Step 2: Commit**

```bash
git add cogs/roblox_stats.py
git commit -m "style: format concluded match scoreboard as ascii table"
```

### Task 2: Refactor `process_live_update` Scoreboard Formatting

**Files:**
- Modify: `d:\main\coding\discord-bot\cogs\roblox_stats.py`

**Interfaces:**
- Consumes: `payload` containing live `stats`.

- [ ] **Step 1: Write the updated implementation for `process_live_update`**

Update the portion inside `process_live_update` where `description` is built from stats:

```python
            stats = payload.get('stats', [])
            if not stats:
                description = "No stats recorded yet this round."
            else:
                max_name_len = max(len(stat.get('Name', 'Unknown')) for stat in stats)
                name_width = max(max_name_len, 16)
                
                description = "```\n"
                description += f"{'Player':<{name_width}} | K  | D  | A\n"
                description += "-" * (name_width + 17) + "\n"
                
                for stat in stats:
                    name = stat.get('Name', 'Unknown')
                    k = stat.get('Kills', 0)
                    d = stat.get('Deaths', 0)
                    a = stat.get('Assists', 0)
                    description += f"{name:<{name_width}} | {k:<2} | {d:<2} | {a:<2}\n"
                
                description += "```"
                
            killfeed_msg = payload.get("killfeed_message", "")
            if killfeed_msg:
                description = f"🔥 **LIVE KILLFEED:** {killfeed_msg}\n\n" + description
```

- [ ] **Step 2: Commit**

```bash
git add cogs/roblox_stats.py
git commit -m "style: format live match scoreboard as ascii table"
```
