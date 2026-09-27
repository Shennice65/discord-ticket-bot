# Roblox Match Administration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a Session System that allows users to start named matches (e.g., "Merl vs Senpai"), resetting the round counter without deleting previous matches. Old match messages will continue to function independently.

**Architecture:** 
- `!roblox_match [name]` command will start a new session, reset the internal round counter to 0, and send a new tracker message with the custom name.
- We will tie database records to the specific Discord `message_id` of the tracker message. This guarantees that old match dropdowns only query rounds from their specific match, and new ones query their own rounds.
- The `RobloxMatchSelect` view will use a static `custom_id` and rely on `interaction.message.id` to fetch the correct database record, ensuring it survives bot restarts.

**Tech Stack:** Python, Discord.py (ext.commands), Motor (MongoDB AsyncIO).

## Global Constraints

- Commands must require Administrator permissions (`@commands.has_permissions(administrator=True)`).
- The Dropdown must cleanly handle interactions on any historical match message.

---

### Task 1: Update the RobloxStats Discord UI for Persistent Sessions

**Files:**
- Modify: `cogs/roblox_stats.py`

**Interfaces:**
- Consumes: `discord.ui.View` and `discord.ui.Select`
- Produces: A persistent view architecture utilizing `interaction.message.id`.

- [ ] **Step 1: Refactor View and Select for Persistence**
Make `RobloxMatchSelect` use a static `custom_id="roblox_match_select"`.
In its `callback`, fetch the match using the message ID:
```python
    async def callback(self, interaction: discord.Interaction):
        try:
            round_num = int(self.values[0])
            # Fetch match using BOTH the round number and the message ID it belongs to!
            match = await self.cog.bot.db.db.roblox_matches.find_one({
                "message_id": interaction.message.id,
                "round_num": round_num
            })
            
            if not match:
                await interaction.response.send_message("Match not found in database.", ephemeral=True)
                return

            embed = self.cog.build_match_embed(match)
            await interaction.response.edit_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
```

- [ ] **Step 2: Update `get_all_matches` to require a `message_id`**
```python
    async def get_all_matches(self, message_id: int):
        cursor = self.bot.db.db.roblox_matches.find({"message_id": message_id}).sort("round_num", 1)
        return await cursor.to_list(length=100)
```

- [ ] **Step 3: Register the persistent view in the Cog's `__init__`**
*(Note: Because the options are dynamic, we must register a blank view so Discord routes the custom_id to our cog after a restart).*
```python
class RobloxStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Register a dummy view with the select so custom_id routing works on restart
        self.bot.add_view(RobloxMatchView(self, [], register_only=True))
```
*Note: Update `RobloxMatchView` to handle `register_only` by adding a dummy select with the correct custom_id if True.*


### Task 2: Implement UI Refresh Helper

**Files:**
- Modify: `cogs/roblox_stats.py`

- [ ] **Step 1: Write `refresh_ui` implementation**
```python
    async def refresh_ui(self):
        """Helper to re-fetch matches and update the current persistent Discord UI message."""
        channel_id = await self.bot.db.get_setting("roblox_stats_channel")
        msg_id = await self.bot.db.get_setting("roblox_stats_message")
        match_name = await self.bot.db.get_setting("roblox_match_name") or "Roblox Match Stats"
        
        if not channel_id or not msg_id:
            return
            
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
            
        try:
            msg = await channel.fetch_message(msg_id)
            matches = await self.get_all_matches(msg_id)
            view = RobloxMatchView(self, matches)
            
            if matches:
                embed = self.build_match_embed(matches[-1])
            else:
                embed = discord.Embed(title=f"🏁 {match_name}", description="Waiting for the next round to finish...", color=0x00FF00)
                
            await msg.edit(embed=embed, view=view)
        except discord.NotFound:
            pass
```

- [ ] **Step 2: Update `process_new_match` to use `refresh_ui` and `message_id`**
Store `match_data["message_id"] = msg_id`.
Call `await self.refresh_ui()`.


### Task 3: Implement Named Match Administration Commands

**Files:**
- Modify: `cogs/roblox_stats.py`

- [ ] **Step 1: Write `!roblox_match` command**
Replace `set_roblox_channel` with this command.
```python
    @commands.command(aliases=["roblox_new", "set_roblox_channel"])
    @commands.has_permissions(administrator=True)
    async def roblox_match(self, ctx, *, match_name: str = "Roblox Match Stats"):
        """Starts a new named match session and resets the round counter to 0."""
        await self.bot.db.set_setting("roblox_stats_channel", ctx.channel.id)
        await self.bot.db.set_setting("roblox_match_name", match_name)
        
        # Reset the round counter
        await self.bot.db.bot_settings.update_one(
            {"key": "roblox_round_counter"},
            {"$set": {"value": 0}},
            upsert=True
        )
        
        embed = discord.Embed(title=f"🏁 {match_name}", description="Waiting for the next round to finish...", color=0x00FF00)
        msg = await ctx.send(embed=embed)
        await self.bot.db.set_setting("roblox_stats_message", msg.id)
        
        # Now refresh UI to attach the dropdown view
        await self.refresh_ui()
        await ctx.send(f"✅ Started new match: **{match_name}**. Old matches are preserved!", ephemeral=True)
```

- [ ] **Step 2: Write `!roblox_delete` command**
```python
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def roblox_delete(self, ctx, round_num: int):
        """Deletes a specific round from the CURRENT active match."""
        msg_id = await self.bot.db.get_setting("roblox_stats_message")
        if not msg_id:
            return await ctx.send("No active match configured.", ephemeral=True)
            
        result = await self.bot.db.db.roblox_matches.delete_one({"message_id": msg_id, "round_num": round_num})
        if result.deleted_count > 0:
            await self.refresh_ui()
            await ctx.send(f"✅ Successfully deleted Round {round_num} from the current match.", ephemeral=True)
        else:
            await ctx.send(f"❌ Round {round_num} was not found in the current match.", ephemeral=True)
```
