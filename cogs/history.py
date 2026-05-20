import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import asyncio

from config import Config
from database import Database
from utils.embeds import TicketEmbeds

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

class History(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
    
    @app_commands.command(name="history", description="View a user's ranked and observation history")
    @app_commands.describe(user="The user to check history for (defaults to yourself)")
    async def history(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target_user = user or interaction.user
        
        observer_role = interaction.guild.get_role(Config.OBSERVER_ROLE_ID)
        is_observer = observer_role in interaction.user.roles if observer_role else False
        
        if not is_observer and target_user.id != interaction.user.id:
            await interaction.response.send_message("You can only view your own history!", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        await self.db.init()
        history = await self.db.get_user_history(target_user.id)
        embed = TicketEmbeds.history_embed(target_user, history)
        
        if is_observer:
            view = ClearHistoryView(target_user.id, target_user.name)
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="clearhistory", description="Clear a user's history (Observer only)")
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
        observer_role = interaction.guild.get_role(Config.OBSERVER_ROLE_ID)
        is_observer = observer_role in interaction.user.roles if observer_role else False
        
        if not is_observer:
            await interaction.response.send_message("Only observers can clear history!", ephemeral=True)
            return
        
        modal = ConfirmClearModal(user.id, user.name, type.value)
        await interaction.response.send_modal(modal)

async def setup(bot):
    await bot.add_cog(History(bot))