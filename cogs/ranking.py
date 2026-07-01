import discord
from discord.ext import commands
from discord import app_commands
import re
from typing import List, Optional
from datetime import datetime

from database import Database
from config import Config

TIERS = ["Phantoms", "Champions", "Legends", "Masters", "Novices"]

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
        content = await cog.generate_leaderboard_content(self.current_page)
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary, custom_id="ranking_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('Ranking')
        if not cog: return
        self.current_page = min(4, self.current_page + 1)
        content = await cog.generate_leaderboard_content(self.current_page)
        await interaction.response.edit_message(content=content, view=self)

class LeaderboardLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="View Leaderboard", style=discord.ButtonStyle.primary, custom_id="view_leaderboard_btn", emoji="🏆")
    async def view_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('Ranking')
        if not cog: 
            await interaction.response.send_message("Bot is starting up...", ephemeral=True)
            return
            
        content = await cog.generate_leaderboard_content(0)
        view = RankingPaginationView(0)
        await interaction.response.send_message(content=content, view=view, ephemeral=True)

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
            # Wait 2 seconds to debounce fast chat messages
            await asyncio.sleep(2.0)
            
            # Fetch old panel ID and delete it
            old_id = await self.db.get_setting("ranking_panel_id")
            if old_id:
                try:
                    old_msg = await channel.fetch_message(old_id)
                    await old_msg.delete()
                except discord.NotFound:
                    pass
                    
            # Spawn new panel
            embed = discord.Embed(
                title="🏆 Server Leaderboard",
                description="Click the button below to view the live ranking leaderboard!",
                color=discord.Color.gold()
            )
            view = LeaderboardLauncherView()
            new_msg = await channel.send(embed=embed, view=view)
            
            # Save new ID
            await self.db.set_setting("ranking_panel_id", new_msg.id)
        except asyncio.CancelledError:
            # Task was cancelled by another message, which is fine (debounce)
            pass
        except Exception as e:
            print(f"Error replacing sticky panel: {e}")

    async def generate_leaderboard_content(self, page_index: int) -> str:
        tier_name = TIERS[page_index]
        all_ranks = await self.db.get_all_player_ranks()
        
        # Filter and parse
        tier_players = []
        for r in all_ranks:
            parsed = parse_rank(r.get('rank', ''))
            if parsed and parsed[0] == tier_name:
                tier_players.append((r['user_id'], parsed[1]))
                
        # Sort by number ascending (lower number is better)
        tier_players.sort(key=lambda x: x[1])
        
        desc = f"# 🏆 {tier_name} Leaderboard\n\n"
        
        if not tier_players:
            desc += "No players in this rank yet.\n"
        else:
            for i, (uid, num) in enumerate(tier_players, 1):
                desc += f"**{i}.** <@{uid}>\n"
                
        desc += f"\n*Page {page_index + 1} of 5*"
        return desc

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

async def setup(bot):
    await bot.add_cog(Ranking(bot))
