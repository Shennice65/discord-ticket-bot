import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import asyncio

from config import Config
from database import Database
from utils.embeds import TicketEmbeds

def can_clear_history(user: discord.Member | discord.User) -> bool:
    if user.id == Config.MASTER_ADMIN_ID:
        return True
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator
    return False

def can_view_history(user: discord.Member, guild: discord.Guild) -> bool:
    if can_clear_history(user):
        return True
    observer_role = guild.get_role(Config.OBSERVER_ROLE_ID)
    if observer_role and observer_role in user.roles:
        return True
    if hasattr(Config, 'TRIAL_OBSERVER_ROLE_ID'):
        trial_observer_role = guild.get_role(Config.TRIAL_OBSERVER_ROLE_ID)
        if trial_observer_role and trial_observer_role in user.roles:
            return True
    return False

class ClearHistoryView(discord.ui.View):
    def __init__(self, user_id: int, user_name: str):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.user_name = user_name
        self.confirmed = False
        self.clear_type = None
    
    @discord.ui.button(label="Clear Ranked 1v1", style=discord.ButtonStyle.danger, custom_id="clear_ranked")
    async def clear_ranked(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ConfirmClearModal(self.user_id, self.user_name, "ranked")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Clear Observations", style=discord.ButtonStyle.danger, custom_id="clear_obs")
    async def clear_obs(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ConfirmClearModal(self.user_id, self.user_name, "observations")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_clear")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Clear cancelled.", embed=None, view=None)

class ConfirmClearModal(discord.ui.Modal, title="Confirm Clear History"):
    def __init__(self, user_id: int, user_name: str, clear_type: str):
        super().__init__()
        self.user_id = user_id
        self.user_name = user_name
        self.clear_type = clear_type
        
        type_text = "Ranked 1v1" if clear_type == "ranked" else "Personal Observations"
        
        self.confirm_username = discord.ui.TextInput(
            label=f"Type '{user_name}' to confirm",
            placeholder=f"Enter exactly: {user_name}",
            required=True,
            max_length=100
        )
        self.add_item(self.confirm_username)
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm_username.value != self.user_name:
            await interaction.response.send_message(
                f"Username mismatch! You typed '{self.confirm_username.value}' but needed '{self.user_name}'.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        db = Database()
        await db.init()
        
        if self.clear_type == "ranked":
            deleted_count = await db.clear_ranked_history(self.user_id)
            type_name = "Ranked 1v1"
        else:
            deleted_count = await db.clear_observation_history(self.user_id)
            type_name = "Personal Observations"
        
        log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
        if log_channel:
            target_user = await interaction.client.fetch_user(self.user_id)
            embed = discord.Embed(
                title="History Cleared",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Target User", value=f"{target_user.mention} ({target_user.name})", inline=True)
            embed.add_field(name="Cleared By", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
            embed.add_field(name="Type", value=type_name, inline=True)
            embed.add_field(name="Entries Deleted", value=str(deleted_count), inline=True)
            embed.set_footer(text=f"User ID: {self.user_id}")
            await log_channel.send(embed=embed)
        
        embed = discord.Embed(
            title="History Cleared",
            description=f"Successfully cleared **{deleted_count}** {type_name} entries for **{self.user_name}**.",
            color=discord.Color.green()
        )
        
        await interaction.edit_original_response(content=None, embed=embed, view=None)

class HistoryView(discord.ui.View):
    def __init__(self, target_user: discord.Member, history: dict, unrank_info: dict = None, obs_cooldown_days: float = 0.0, ranked_cooldown_days: float = 0.0, is_observer: bool = False, current_rank: str = "Unranked"):
        super().__init__(timeout=180)
        self.target_user = target_user
        self.history = history
        self.unrank_info = unrank_info
        self.obs_cooldown_days = obs_cooldown_days
        self.ranked_cooldown_days = ranked_cooldown_days
        self.is_admin = is_observer
        self.current_rank = current_rank

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary, custom_id="hist_overview")
    async def btn_overview(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = TicketEmbeds.history_overview_embed(self.target_user, self.history, self.unrank_info, self.obs_cooldown_days, self.ranked_cooldown_days, current_rank=self.current_rank)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="Ranked Matches", style=discord.ButtonStyle.secondary, custom_id="hist_ranked")
    async def btn_ranked(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = TicketEmbeds.history_ranked_embed(self.target_user, self.history)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="Observations", style=discord.ButtonStyle.secondary, custom_id="hist_obs")
    async def btn_obs(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = TicketEmbeds.history_observation_embed(self.target_user, self.history)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="Clear History", style=discord.ButtonStyle.danger, custom_id="hist_clear")
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_admin:
            await interaction.response.send_message("Only administrators can clear history.", ephemeral=True)
            return
            
        view = ClearHistoryView(self.target_user.id, self.target_user.name)
        await interaction.response.send_message("Select history to clear:", view=view, ephemeral=True)

class History(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
    
    @app_commands.command(name="history", description="View a user's ranked and observation history")
    @app_commands.describe(user="The user to check history for (defaults to yourself)")
    async def history(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target_user = user or interaction.user
        
        if target_user.id == Config.MASTER_ADMIN_ID and interaction.user.id != Config.MASTER_ADMIN_ID:
            await interaction.response.send_message("<:locke:1537515688908824627> you can not view this person history", ephemeral=True)
            return
        
        is_admin = can_clear_history(interaction.user)
        
        await interaction.response.defer(ephemeral=True)
        
        history = await self.db.get_user_history(target_user.id, target_user.name)
        obs_cooldown_days = await self.db.get_obs_cooldown(target_user.id)
        ranked_cooldown_hours = await self.db.get_ranked_cooldown(target_user.id)
        ranked_cooldown_days = ranked_cooldown_hours / 24.0
        
        player = await self.db.player_ranks.find_one({"user_id": target_user.id})
        current_rank = player.get("rank", "Unranked") if player else "Unranked"
        unrank_info = None
        if player and player.get("unranked_at"):
            cooldown = self.db._get_unrank_cooldown_days(player)
            unrank_info = {
                "original_rank": player.get("original_rank", "Unknown"),
                "cooldown_days": cooldown
            }
        
        embed = TicketEmbeds.history_overview_embed(target_user, history, unrank_info=unrank_info, obs_cooldown_days=obs_cooldown_days, ranked_cooldown_days=ranked_cooldown_days, current_rank=current_rank)
        view = HistoryView(target_user, history, unrank_info, obs_cooldown_days, ranked_cooldown_days, is_admin, current_rank)
        
        if not is_admin:
            view.remove_item(view.btn_clear)
            
        await interaction.followup.send(embed=embed, view=view)
    
    @app_commands.command(name="clearhistory", description="Clear a user's history (Admin only)")
    @app_commands.describe(user="The user to clear history for", type="Type of history to clear")
    @app_commands.choices(type=[
        app_commands.Choice(name="Ranked 1v1", value="ranked"),
        app_commands.Choice(name="Personal Observations", value="observations"),
        app_commands.Choice(name="Both", value="both")
    ])
    async def clearhistory_direct(
        self, 
        interaction: discord.Interaction, 
        user: discord.Member, 
        type: app_commands.Choice[str]
    ):
        if not can_clear_history(interaction.user):
            await interaction.response.send_message("Only administrators can clear history!", ephemeral=True)
            return
        
        modal = ConfirmClearModal(user.id, user.name, type.value)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="h2h", description="View head-to-head stats between two players")
    @app_commands.describe(player1="First player", player2="Second player")
    async def h2h(self, interaction: discord.Interaction, player1: discord.Member, player2: discord.Member):
        if player1.id == player2.id:
            await interaction.response.send_message("You must select two different players!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        h2h_data = await self.db.get_h2h(player1.id, player2.id)
        
        embed = TicketEmbeds.h2h_embed(player1, player2, h2h_data)
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(History(bot))
