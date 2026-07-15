import discord
from discord.ext import commands
from discord import app_commands
import re
from typing import List, Optional
from datetime import datetime

from database import Database
from config import Config

TIERS = ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]

def is_admin_or_observer(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    observer_role = interaction.guild.get_role(Config.OBSERVER_ROLE_ID)
    if observer_role and observer_role in interaction.user.roles:
        return True
    return False

def parse_rank(rank_str: str):
    """Returns (tier_name, number) or None if invalid."""
    match = re.match(r'^([a-zA-Z]+)\s*(\d+)$', rank_str)
    if not match:
        return None
    tier_name = match.group(1).capitalize()
    number = int(match.group(2))
    return (tier_name, number)

class RankingPaginationView(discord.ui.View):
    def __init__(self, current_page=0):
        super().__init__(timeout=None)
        self.current_page = current_page
        
    @discord.ui.button(label="◀️ Back", style=discord.ButtonStyle.secondary, custom_id="ranking_back")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('Ranking')
        if not cog: return
        self.current_page = max(0, self.current_page - 1)
        embeds, file = await cog.generate_leaderboard_content(self.current_page)
        attachments = [file] if file else []
        await interaction.response.edit_message(content=None, embeds=embeds, attachments=attachments, view=self)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary, custom_id="ranking_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('Ranking')
        if not cog: return
        self.current_page = min(len(TIERS) - 1, self.current_page + 1)
        embeds, file = await cog.generate_leaderboard_content(self.current_page)
        attachments = [file] if file else []
        await interaction.response.edit_message(content=None, embeds=embeds, attachments=attachments, view=self)

class LeaderboardLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="View Leaderboard", style=discord.ButtonStyle.primary, custom_id="view_leaderboard_btn", emoji="🏆")
    async def view_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('Ranking')
        if not cog: 
            await interaction.response.send_message("Bot is starting up...", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        embeds, file = await cog.generate_leaderboard_content(0)
        view = RankingPaginationView(0)
        kwargs = {"embeds": embeds, "view": view, "ephemeral": True}
        if file:
            kwargs["file"] = file
        await interaction.followup.send(**kwargs)

    @discord.ui.button(label="View Observers", style=discord.ButtonStyle.secondary, custom_id="view_observers_btn", emoji="👀")
    async def view_observers(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
            
        role = interaction.guild.get_role(Config.OBSERVER_ROLE_ID)
        if not role:
            await interaction.response.send_message("Observer role not found or not configured.", ephemeral=True)
            return
            
        observers = [member for member in interaction.guild.members if role in member.roles]
        
        if not observers:
            await interaction.response.send_message("No observers found.", ephemeral=True)
            return
            
        # Format the observers
        desc = "# 👀 Server Observers\n\n"
        for observer in observers:
            desc += f"→ {observer.display_name} {observer.mention}\n"
                
        await interaction.response.send_message(desc, ephemeral=True)


class Ranking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.bot.loop.create_task(self.db.init())
        self._panel_task = None
        
    @commands.Cog.listener()
    async def on_ready(self):
        print("Ranking cog loaded")
        # Register both views so they persist after restart
        self.bot.add_view(RankingPaginationView())
        self.bot.add_view(LeaderboardLauncherView())
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not Config.RANKING_PANEL_CHANNEL_ID or message.channel.id != Config.RANKING_PANEL_CHANNEL_ID:
            return
            
        # Ignore if the message IS the panel itself
        if message.author == self.bot.user and message.embeds and message.embeds[0].title == "🏆 Server Leaderboard":
            return
            
        # Cancel pending task if any, to debounce
        if self._panel_task and not self._panel_task.done():
            self._panel_task.cancel()
            
        self._panel_task = self.bot.loop.create_task(self._replace_panel(message.channel))
        
    async def _replace_panel(self, channel: discord.TextChannel):
        import asyncio
        try:
            # Wait 10 seconds to debounce fast chat messages
            await asyncio.sleep(10.0)
            
            # Fetch old panel ID
            old_id = await self.db.get_setting("ranking_panel_id")
            
            # Check if the panel is already near the bottom (within last 5 messages)
            if old_id:
                recent_messages = []
                async for msg in channel.history(limit=5):
                    recent_messages.append(msg.id)
                
                if old_id in recent_messages:
                    # Panel is still very visible, no need to bump and spam notifications
                    return
            
            # If it's pushed too far up, delete the old one
            if old_id:
                try:
                    old_msg = await channel.fetch_message(old_id)
                    await old_msg.delete()
                except discord.NotFound:
                    pass
                    
            # Spawn new panel silently to avoid pinging
            embed = discord.Embed(
                title="🏆 Server Leaderboard",
                description="Click the button below to view the live ranking leaderboard!",
                color=discord.Color.gold()
            )
            view = LeaderboardLauncherView()
            new_msg = await channel.send(embed=embed, view=view, silent=True)
            
            # Save new ID
            await self.db.set_setting("ranking_panel_id", new_msg.id)
        except asyncio.CancelledError:
            # Task was cancelled by another message, which is fine (debounce)
            pass
        except Exception as e:
            print(f"Error replacing sticky panel: {e}")

    async def generate_leaderboard_content(self, page_index: int) -> tuple[list[discord.Embed], Optional[discord.File]]:
        from utils.podium_generator import get_podium_image
        import discord
        import re
        
        tier_name = TIERS[page_index]
        all_ranks = await self.db.get_all_player_ranks()
        
        # Filter and parse
        tier_players = []
        for r in all_ranks:
            parsed = parse_rank(r.get('rank', ''))
            if parsed and parsed[0] == tier_name:
                streak = r.get('win_streak', 0)
                tier_players.append((r['user_id'], parsed[1], streak))
                
        # Sort by number ascending (lower number is better)
        tier_players.sort(key=lambda x: x[1])
        
        desc = f"# 🏆 {tier_name} Leaderboard\n\n"
        file = None
        
        if not tier_players:
            desc += "No players in this rank yet.\n"
        else:
            top_3 = []
            for i in range(min(3, len(tier_players))):
                uid = tier_players[i][0]
                user = self.bot.get_user(uid)
                if not user:
                    try:
                        user = await self.bot.fetch_user(uid)
                    except:
                        pass
                
                avatar_url = user.display_avatar.url if user else ""
                raw_display = user.display_name if user else f"Player {uid}"
                display_name = re.sub(r'\s*\(@[^)]+\)', '', raw_display).strip()
                username = user.name if user else f"Player {uid}"
                top_3.append((uid, avatar_url, display_name, username))
                
            while len(top_3) < 3:
                top_3.append((0, "", "", ""))
                
            podium_path = await get_podium_image(tier_name, top_3)
            file = discord.File(podium_path, filename="podium.png")
            
            medals = ["🥇", "🥈", "🥉"]
            # Build a name cache from the top 3 we already fetched
            name_cache = {t[0]: (t[2], t[3]) for t in top_3 if t[0] != 0}
            for i, (uid, num, streak) in enumerate(tier_players[:3]):
                display_name, username = name_cache.get(uid, ("Unknown User", "Unknown User"))
                if display_name.lower() == username.lower():
                    name_text = f"**{display_name}**"
                else:
                    name_text = f"**{display_name}** (@{username})"
                streak_text = f" `🔥{streak}`" if streak >= 2 else ""
                desc += f"{medals[i]} {name_text}{streak_text}\n"
                
            if len(tier_players) > 3:
                desc += "\n**Runners Up**\n"
                for i, (uid, num, streak) in enumerate(tier_players[3:], 4):
                    # Use guild cache only — no API calls to avoid rate limits
                    member = None
                    for guild in self.bot.guilds:
                        member = guild.get_member(uid)
                        if member:
                            break
                    raw_display = member.display_name if member else "Unknown User"
                    display_name = re.sub(r'\s*\(@[^)]+\)', '', raw_display).strip()
                    username = member.name if member else "Unknown User"
                    if display_name.lower() == username.lower():
                        name_text = f"**{display_name}**"
                    else:
                        name_text = f"**{display_name}** (@{username})"
                    streak_text = f" `🔥{streak}`" if streak >= 2 else ""
                    desc += f"`#{i}` {name_text}{streak_text}\n"
                
        desc += f"\n*Page {page_index + 1} of {len(TIERS)}*"
        
        embeds = []
        if file:
            image_embed = discord.Embed(color=discord.Color(0x2b2d31))
            image_embed.set_image(url="attachment://podium.png")
            embeds.append(image_embed)
            
        text_embed = discord.Embed(description=desc, color=discord.Color(0x2b2d31))
        embeds.append(text_embed)
            
        return embeds, file

    @app_commands.command(name="setupranking", description="Setup the live ranking leaderboard button in this channel")
    @app_commands.default_permissions(administrator=True)
    async def setupranking(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        embed = discord.Embed(
            title="🏆 Server Leaderboard",
            description="Click the button below to view the live ranking leaderboard!",
            color=discord.Color.gold()
        )
        
        view = LeaderboardLauncherView()
        new_msg = await interaction.channel.send(embed=embed, view=view)
        
        await self.db.set_setting("ranking_panel_id", new_msg.id)
        await interaction.followup.send("Ranking button setup complete!", ephemeral=True)

    @app_commands.command(name="removeplayer", description="Remove a player from the leaderboard and shift everyone else up")
    async def remove_player(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        success = await self.db.remove_player_from_ladder(user.id)
        if success:
            await interaction.followup.send(f"Successfully removed {user.mention} from the leaderboard! The ladder has been compressed to fill their gap.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🔴 Player Removed from Ladder",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Removed By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"{user.mention} is not currently ranked on the leaderboard.", ephemeral=True)

    @app_commands.command(name="setrank", description="Manually force a player into a specific rank, shifting others to make room")
    @app_commands.describe(user="The player to rank", rank="The exact rank (e.g., Legends 3)")
    async def set_rank(self, interaction: discord.Interaction, user: discord.User, rank: str):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        success, actual_rank = await self.db.force_set_player_rank(user.id, rank, bypass_unrank=True)
        if success:
            await interaction.followup.send(f"Successfully slotted {user.mention} in at **{actual_rank}**! The rest of the ladder has been compressed and shifted automatically to make room.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🟡 Rank Manually Set",
                    color=discord.Color.gold(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Set By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.add_field(name="New Rank", value=f"**{actual_rank}**", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Failed to set rank. Please ensure the rank is formatted correctly (e.g., `Legends 3`, `Champions 12`).", ephemeral=True)
            
    @app_commands.command(name="setstreak", description="Manually set a player's win streak")
    @app_commands.describe(user="The player to modify", streak="The new win streak (number)")
    async def set_streak(self, interaction: discord.Interaction, user: discord.User, streak: int):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        if streak < 0:
            await interaction.followup.send("Streak cannot be negative.", ephemeral=True)
            return
            
        player = await self.db.player_ranks.find_one({"user_id": user.id})
        if not player or not player.get("rank"):
            await interaction.followup.send(f"{user.mention} is not currently ranked on the leaderboard.", ephemeral=True)
            return
            
        old_streak = player.get("win_streak", 0)
        await self.db.player_ranks.update_one({"user_id": user.id}, {"$set": {"win_streak": streak}})
        
        await interaction.followup.send(f"Successfully set {user.mention}'s win streak to **{streak}**!", ephemeral=True)
        
        log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="🔥 Streak Manually Set",
                color=discord.Color.orange(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Player", value=user.mention, inline=True)
            embed.add_field(name="Change", value=f"{old_streak} ➔ {streak}", inline=True)
            embed.add_field(name="Observer", value=interaction.user.mention, inline=False)
            try:
                await log_channel.send(embed=embed)
            except Exception as e:
                print(f"Failed to send streak log: {e}")

    @app_commands.command(name="resetrequest", description="Reset a player's 24h ranked match request cooldown")
    async def reset_request(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        success = await self.db.reset_ranked_cooldown(user.id)
        if success:
            await interaction.followup.send(f"✅ Reset ranked request cooldown for {user.mention}! They can now request another match immediately.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🔵 Request Cooldown Reset",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Reset By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"{user.mention} does not currently have an active cooldown.", ephemeral=True)
            
    @app_commands.command(name="clearunrank", description="Clear a player's unrank penalty (1-month re-rank ban and R1 restriction)")
    async def clear_unrank(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        success = await self.db.clear_unrank_penalty(user.id)
        if success:
            await interaction.followup.send(f"Cleared unrank penalty for {user.mention}. They can now be re-ranked and request R1s freely.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🟢 Unrank Penalty Cleared",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Cleared By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"{user.mention} does not have an active unrank penalty.", ephemeral=True)

    @app_commands.command(name="removebyrank", description="Remove a player from the leaderboard by specifying their rank (e.g., Legends 3)")
    @app_commands.describe(rank="The exact rank to remove (e.g., Legends 3)")
    async def remove_by_rank(self, interaction: discord.Interaction, rank: str):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
            
        from ladder_utils import parse_rank
        parsed = parse_rank(rank)
        if not parsed:
            await interaction.response.send_message(f"❌ Invalid rank format. Please use a format like `Legends 3`.", ephemeral=True)
            return
            
        formatted_rank = f"{parsed[0]} {parsed[1]}"
        
        await interaction.response.defer(ephemeral=True)
        
        player = await self.db.get_player_by_rank(formatted_rank)
        
        if not player:
            await interaction.followup.send(f"❌ No player is currently ranked at **{formatted_rank}**.", ephemeral=True)
            return
            
        user_id = player['user_id']
        
        # Try to get the user object for display purposes
        user = interaction.guild.get_member(user_id)
        
        success = await self.db.remove_player_from_ladder(user_id)
        
        if success:
            user_mention = user.mention if user else f"<@{user_id}>"
            user_name = user.name if user else f"User {user_id}"
            
            await interaction.followup.send(f"Successfully removed {user_mention} from **{formatted_rank}**! The ladder has been compressed.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                from datetime import datetime
                embed = discord.Embed(
                    title="🔴 Player Removed from Ladder (By Rank)",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user_mention}\n`{user_name}`", inline=True)
                embed.add_field(name="Removed From Rank", value=f"**{formatted_rank}**", inline=True)
                embed.add_field(name="Removed By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user_id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"Failed to remove player from {formatted_rank}.", ephemeral=True)

    @app_commands.command(name="checkrank", description="Check a user's current rank on the leaderboard")
    @app_commands.describe(user="The user to check (defaults to yourself)")
    async def check_rank(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target_user = user or interaction.user
        
        await interaction.response.defer(ephemeral=True)
        
        rank = await self.db.get_player_rank(target_user.id)
        
        if rank:
            await interaction.followup.send(f"{target_user.mention} is currently ranked at **{rank}**.", ephemeral=True)
        else:
            await interaction.followup.send(f"{target_user.mention} is currently **Unranked**.", ephemeral=True)

    @app_commands.command(name="undo", description="Undo the most recent rank change for a specific user")
    @app_commands.describe(user="The user whose rank change you want to undo")
    async def undo(self, interaction: discord.Interaction, user: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        success, message = await self.db.undo_last_action(user.id)
        
        if success:
            await interaction.followup.send(f"✅ Undo successful! {user.mention} has been {message}. The leaderboard shifted back.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                from datetime import datetime
                embed = discord.Embed(
                    title="↩️ Rank Action Undone",
                    color=discord.Color.purple(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Target", value=f"{user.mention}\n`{user.name}`", inline=True)
                embed.add_field(name="Action Result", value=f"They were {message}", inline=False)
                embed.add_field(name="Undone By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                embed.set_footer(text=f"User ID: {user.id}")
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Undo failed: {message}", ephemeral=True)

    @app_commands.command(name="botversion", description="Check the current version of the bot")
    async def check_version(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🤖 **Ticket Bot Version:** `{Config.VERSION}`", ephemeral=True)

    @app_commands.command(name="allowrematch", description="Reset the 24h rematch cooldown between two players (Observer only)")
    @app_commands.describe(player1="First player", player2="Second player")
    async def allow_rematch(self, interaction: discord.Interaction, player1: discord.User, player2: discord.User):
        if not is_admin_or_observer(interaction):
            await interaction.response.send_message("Only Admins or Observers can use this command!", ephemeral=True)
            return
        
        if player1.id == player2.id:
            await interaction.response.send_message("You must select two different players!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        # Check if there's even an active cooldown
        cooldown = await self.db.get_rematch_cooldown(player1.id, player2.id)
        if cooldown <= 0:
            await interaction.followup.send(f"There is no active rematch cooldown between {player1.mention} and {player2.mention}.", ephemeral=True)
            return
        
        success = await self.db.reset_rematch_cooldown(player1.id, player2.id)
        if success:
            await interaction.followup.send(f"✅ Rematch cooldown cleared! {player1.mention} and {player2.mention} can now face each other again.", ephemeral=True)
            
            log_channel = interaction.guild.get_channel(Config.RANK_LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="🔄 Rematch Cooldown Cleared",
                    color=discord.Color.teal(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Player 1", value=f"{player1.mention}\n`{player1.name}`", inline=True)
                embed.add_field(name="Player 2", value=f"{player2.mention}\n`{player2.name}`", inline=True)
                embed.add_field(name="Cleared By", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
                await log_channel.send(embed=embed)
        else:
            await interaction.followup.send("Failed to clear rematch cooldown. No recent match found between these players.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Ranking(bot))
