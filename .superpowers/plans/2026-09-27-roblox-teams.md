# Team Scoreboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modify the Roblox integration to group players by their in-game Team and display split team scoreboards in Discord.

**Architecture:** 
1. The Roblox Lua script will track each player's `Team.Name` and send it in the JSON payload under `stats`. 
2. The Python Discord bot will read the `Team` field, group the players, and render a separate ASCII table for each team in the Embed description.

**Tech Stack:** Python, discord.py, Roblox Lua (Luau)

## Global Constraints

- The Python embed max description length is 4096 characters; ensure the tables are compact.
- If a player has no team, fallback to "Unknown" or "Solo".

---

### Task 1: Update Python Bot to Render Team Scoreboards

**Files:**
- Modify: `d:\main\coding\discord-bot\cogs\roblox_stats.py`

**Interfaces:**
- Consumes: JSON `stats` arrays where each object now includes a `Team` string property.
- Produces: An embed with separate tables for each team.

- [ ] **Step 1: Create a helper function `format_team_tables`**
Add this function to the `RobloxStats` class.

```python
    def format_team_tables(self, stats: list) -> str:
        if not stats:
            return "No stats recorded yet."
            
        teams = {}
        for stat in stats:
            team_name = stat.get("Team", "No Team")
            if team_name not in teams:
                teams[team_name] = []
            teams[team_name].append(stat)
            
        max_name_len = max(len(stat.get('Name', 'Unknown')) for stat in stats)
        name_width = max(max_name_len, 14)
        
        description = ""
        for team_name, members in teams.items():
            description += f"**🛡️ {team_name} Team**\n```\n"
            description += f"{'Player':<{name_width}} | K  | D  | A\n"
            description += "-" * (name_width + 17) + "\n"
            
            for stat in members:
                name = stat.get('Name', 'Unknown')
                k = stat.get('Kills', 0)
                d = stat.get('Deaths', 0)
                a = stat.get('Assists', 0)
                description += f"{name:<{name_width}} | {k:<2} | {d:<2} | {a:<2}\n"
            
            description += "```\n"
            
        return description
```

- [ ] **Step 2: Update `build_match_embed`**
Modify `build_match_embed` to use the helper.

```python
    def build_match_embed(self, match: dict) -> discord.Embed:
        description = self.format_team_tables(match.get('stats', []))
        
        embed = discord.Embed(
            title=f"🏁 Match Concluded! (Round {match.get('round_num')})",
            description=description,
            color=0x00FF00,
            timestamp=match.get('timestamp') or discord.utils.utcnow()
        )
        return embed
```

- [ ] **Step 3: Update `process_live_update`**
Modify the embed generation inside `process_live_update` to use the helper.

```python
            table_desc = self.format_team_tables(payload.get('stats', []))
            
            killfeed_msg = payload.get("killfeed_message", "")
            if killfeed_msg:
                description = f"🔥 **LIVE KILLFEED:** {killfeed_msg}\n\n" + table_desc
            else:
                description = table_desc
                
            embed = discord.Embed(
                title=f"🔴 LIVE MATCH: {match_name}",
                description=description,
                color=0xFF0000,
                timestamp=discord.utils.utcnow()
            )
```

- [ ] **Step 4: Commit**
```bash
git add cogs/roblox_stats.py
git commit -m "feat: group scoreboard by team"
```

### Task 2: Update Roblox Lua Script

**Files:**
- Modify: Your Roblox Executor Script

**Interfaces:**
- Produces: `MatchStats` with a `Team` key.

- [ ] **Step 1: Update the Roblox script to track Teams**
The user must manually update their executor script. 

Modify `getSortedStats` to include the player's team:
```lua
local function getSortedStats()
	local sortedStats = {}
	for playerName, data in pairs(MatchStats) do
        local plr = Players:FindFirstChild(playerName)
        local teamName = "Unknown"
        if plr and plr.Team then
            teamName = plr.Team.Name
        end
		table.insert(sortedStats, {
            Name = playerName, 
            Kills = data.Kills or 0, 
            Deaths = data.Deaths or 0, 
            Assists = data.Assists or 0,
            Team = teamName
        })
	end
	table.sort(sortedStats, function(a, b) return a.Kills > b.Kills end)
	return sortedStats
end
```
