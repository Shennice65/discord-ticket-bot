# Roblox Scoreboard Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Roblox HTTP endpoints out of the Discord bot and into the `clips` Flask web app. The `clips` app will use the Discord REST API to directly update the scoreboard messages in Discord, bypassing the need for the bot to run a web server.

**Architecture:** 
1. The `clips/app.py` Flask app will expose `POST /api/roblox/live-update` and `POST /api/roblox/match-stats`.
2. These endpoints query the shared MongoDB database to find the currently active `message_id` and `channel_id`.
3. The Flask app formats the ASCII tables and sends a direct HTTP `PATCH` request to the Discord API to update the live embed.

**Tech Stack:** Python, Flask, `requests` library.

---

### Task 1: Add Helper Functions and API Routing to Flask

**Files:**
- Modify: `d:\main\coding\discord-bot\clips\app.py`

**Interfaces:**
- Consumes: The `DISCORD_TOKEN` environment variable and `db` (MongoDB connection).

- [ ] **Step 1: Add Formatting Helper**
Add the ASCII team formatting logic near the bottom of `clips/app.py` (before `app.run`):

```python
def format_team_tables(stats: list) -> str:
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
        description += f"**{team_name} Team**\n```\n"
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

- [ ] **Step 2: Add Live Update Endpoint**
Add the POST route to handle live killfeed updates.

```python
import datetime

@app.route("/api/roblox/live-update", methods=["POST"])
def roblox_live_update():
    try:
        payload = request.json
        active_match = db.roblox_matches.find_one({"is_active": True})
        if not active_match:
            return jsonify({"error": "No active match found"}), 404
            
        channel_id = active_match.get("channel_id")
        message_id = active_match.get("message_id")
        match_name = active_match.get("match_name", "Unknown Match")
        
        table_desc = format_team_tables(payload.get('stats', []))
        killfeed_msg = payload.get("killfeed_message", "")
        if killfeed_msg:
            description = f"🔥 **LIVE KILLFEED:** {killfeed_msg}\n\n" + table_desc
        else:
            description = table_desc
            
        embed = {
            "title": f"🔴 LIVE MATCH: {match_name}",
            "description": description,
            "color": 0xFF0000,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        
        headers = {
            "Authorization": f"Bot {os.getenv('DISCORD_TOKEN')}",
            "Content-Type": "application/json"
        }
        
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
        resp = http_requests.patch(url, headers=headers, json={"embeds": [embed]})
        
        if resp.status_code == 200:
            return jsonify({"status": "success"})
        else:
            return jsonify({"error": "Failed to update Discord", "details": resp.text}), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 3: Add Match Stats (Round End) Endpoint**
Add the POST route to handle the end of a round.

```python
@app.route("/api/roblox/match-stats", methods=["POST"])
def roblox_match_stats():
    try:
        payload = request.json
        active_match = db.roblox_matches.find_one({"is_active": True})
        if not active_match:
            return jsonify({"error": "No active match found"}), 404
            
        channel_id = active_match.get("channel_id")
        
        # Increment round in MongoDB
        db.roblox_matches.update_one(
            {"_id": active_match["_id"]},
            {"$inc": {"round_num": 1}}
        )
        round_num = active_match.get("round_num", 1)
        
        description = format_team_tables(payload.get('stats', []))
        
        embed = {
            "title": f"🏁 Match Concluded! (Round {round_num})",
            "description": description,
            "color": 0x00FF00,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        
        headers = {
            "Authorization": f"Bot {os.getenv('DISCORD_TOKEN')}",
            "Content-Type": "application/json"
        }
        
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        resp = http_requests.post(url, headers=headers, json={"embeds": [embed]})
        
        if resp.status_code == 200:
            return jsonify({"status": "success"})
        else:
            return jsonify({"error": "Failed to send to Discord", "details": resp.text}), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4: Commit**
```bash
git add clips/app.py
git commit -m "feat: roblox scoreboard proxy via Discord REST API"
```
