import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import traceback

class RobloxMatchSelect(discord.ui.Select):
    def __init__(self, cog, matches):
        self.cog = cog
        options = []
        seen_values = set()
        
        # Discord select menus can only have up to 25 options. We show the latest 25.
        for match in matches[-25:]:
            val = str(match['round_num'])
            if val in seen_values:
                continue
            seen_values.add(val)
            
            options.append(discord.SelectOption(
                label=f"Round {match['round_num']}", 
                description=f"{len(match.get('stats', []))} players",
                value=val
            ))
        
        # Reverse to show newest at the top
        options.reverse()
        
        # Ensure it has a fallback option if no matches
        if not options:
            options.append(discord.SelectOption(label="No matches yet", value="0"))
            
        super().__init__(placeholder="Select a previous round...", min_values=1, max_values=1, options=options, custom_id="roblox_match_select")

    async def callback(self, interaction: discord.Interaction):
        try:
            round_num = int(self.values[0])
            if round_num == 0:
                await interaction.response.send_message("No matches recorded for this session yet.", ephemeral=True)
                return
                
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
            traceback.print_exc()
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


class RobloxMatchView(discord.ui.View):
    def __init__(self, cog, matches, register_only=False):
        super().__init__(timeout=None)
        if register_only:
            self.add_item(RobloxMatchSelect(cog, []))
        elif matches:
            self.add_item(RobloxMatchSelect(cog, matches))


class RobloxStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Register a dummy view with the select so custom_id routing works on restart
        self.bot.add_view(RobloxMatchView(self, [], register_only=True))
        self.live_update_loop.start()

    def cog_unload(self):
        self.live_update_loop.cancel()

    @tasks.loop(seconds=3.0)
    async def live_update_loop(self):
        try:
            channel_id = await self.bot.db.get_setting("roblox_stats_channel")
            msg_id = await self.bot.db.get_setting("roblox_stats_message")
            match_name = await self.bot.db.get_setting("roblox_match_name") or "Roblox Match Stats"
            
            if not channel_id or not msg_id:
                return
                
            live_doc = await self.bot.db.db.bot_settings.find_one({"key": "roblox_live_stats"})
            if not live_doc:
                return
                
            last_timestamp = await self.bot.db.get_setting("roblox_live_last_timestamp")
            current_timestamp = live_doc.get("timestamp")
            
            if last_timestamp == current_timestamp:
                return 
                
            await self.bot.db.set_setting("roblox_live_last_timestamp", current_timestamp)
            
            stats = live_doc.get("stats", [])
            killfeed_msg = live_doc.get("killfeed_message", "")
            
            table_desc = self.format_team_tables(stats)
            if killfeed_msg:
                description = f"**LIVE KILLFEED:** {killfeed_msg}\n\n" + table_desc
            else:
                description = table_desc
                
            embed = discord.Embed(
                title=f"LIVE MATCH: {match_name}",
                description=description,
                color=0xFF0000,
                timestamp=discord.utils.utcnow()
            )
            
            channel = self.bot.get_channel(channel_id)
            if not channel:
                return
                
            msg = await channel.fetch_message(msg_id)
            
            # Grab current view
            matches = await self.get_all_matches(msg_id)
            view = RobloxMatchView(self, matches)
            
            await msg.edit(embed=embed, view=view)
            
        except discord.NotFound:
            pass
        except Exception as e:
            pass

    async def get_match(self, round_num: int):
        return await self.bot.db.db.roblox_matches.find_one({"round_num": round_num})

    async def get_all_matches(self, message_id: int):
        cursor = self.bot.db.db.roblox_matches.find({"message_id": message_id}).sort("round_num", 1)
        return await cursor.to_list(length=100) # Keep a reasonable limit for the UI

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

    def build_match_embed(self, match: dict) -> discord.Embed:
        description = self.format_team_tables(match.get('stats', []))
            
        embed = discord.Embed(
            title=f"🏁 Match Concluded! (Round {match.get('round_num')})",
            description=description,
            color=0x00FF00,
            timestamp=match.get('timestamp') or discord.utils.utcnow()
        )
        return embed

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

    @app_commands.command(name="roblox_match", description="Starts a new named match session and resets the round counter.")
    @app_commands.default_permissions(administrator=True)
    async def roblox_match(self, interaction: discord.Interaction, match_name: str = "Roblox Match Stats"):
        """Starts a new named match session and resets the round counter to 0."""
        await self.bot.db.set_setting("roblox_stats_channel", interaction.channel.id)
        await self.bot.db.set_setting("roblox_match_name", match_name)
        
        # Reset the round counter
        await self.bot.db.bot_settings.update_one(
            {"key": "roblox_round_counter"},
            {"$set": {"value": 0}},
            upsert=True
        )
        
        embed = discord.Embed(title=f"🏁 {match_name}", description="Waiting for the next round to finish...", color=0x00FF00)
        
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        await self.bot.db.set_setting("roblox_stats_message", msg.id)
        
        # Now refresh UI to attach the dropdown view
        await self.refresh_ui()
        await interaction.followup.send(f"✅ Started new match: **{match_name}**. Old matches are preserved!", ephemeral=True)

    @app_commands.command(name="roblox_delete", description="Deletes a specific round from the CURRENT active match.")
    @app_commands.default_permissions(administrator=True)
    async def roblox_delete(self, interaction: discord.Interaction, round_num: int):
        """Deletes a specific round from the CURRENT active match."""
        msg_id = await self.bot.db.get_setting("roblox_stats_message")
        if not msg_id:
            return await interaction.response.send_message("No active match configured.", ephemeral=True)
            
        result = await self.bot.db.db.roblox_matches.delete_one({"message_id": msg_id, "round_num": round_num})
        if result.deleted_count > 0:
            await self.refresh_ui()
            await interaction.response.send_message(f"✅ Successfully deleted Round {round_num} from the current match.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Round {round_num} was not found in the current match.", ephemeral=True)

    async def process_new_match(self, payload: dict):
        """Called by the Flask web server when Roblox sends a POST request."""
        # Atomic counter to prevent race conditions causing duplicate round numbers
        result = await self.bot.db.bot_settings.find_one_and_update(
            {"key": "roblox_round_counter"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=True
        )
        round_num = result["value"]
        
        msg_id = await self.bot.db.get_setting("roblox_stats_message")
        
        match_data = {
            "message_id": msg_id,
            "round_num": round_num,
            "stats": payload.get("stats", []),
            "timestamp": discord.utils.utcnow()
        }
        
        await self.bot.db.db.roblox_matches.insert_one(match_data)
        
        # Now update the Discord message
        await self.refresh_ui()

    async def process_live_update(self, payload: dict):
        """Called on every kill to update the live Discord embed."""
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
            
            # Update embed but leave the view intact
            await msg.edit(embed=embed)
        except discord.NotFound:
            pass

async def setup(bot):
    await bot.add_cog(RobloxStats(bot))
