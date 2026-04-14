import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime
from typing import Optional

from config import Config
from database import Database
from utils.embeds import TicketEmbeds

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🎮 Ranked 1v1", style=discord.ButtonStyle.primary, custom_id="ranked_1v1")
    async def ranked_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RankedModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="👁️ Personal Observation", style=discord.ButtonStyle.secondary, custom_id="personal_obs")
    async def obs_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ObservationModal()
        await interaction.response.send_modal(modal)

class RankedModal(discord.ui.Modal, title="Ranked 1v1 Ticket"):
    opponent = discord.ui.TextInput(
        label="Opponent's Name",
        placeholder="Enter the opponent's Discord name or IGN",
        required=True,
        max_length=100
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.create_ranked_ticket(interaction, self.opponent.value)

class ObservationModal(discord.ui.Modal, title="Personal Observation Ticket"):
    private_link = discord.ui.TextInput(
        label="Private Server Link (Optional)",
        placeholder="Enter your private server link if applicable",
        required=False,
        max_length=200
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.create_observation_ticket(interaction, self.private_link.value or None)

class CloseRankedModal(discord.ui.Modal, title="Close Ranked 1v1 Ticket"):
    observer = discord.ui.TextInput(
        label="Observer Name",
        placeholder="Your name",
        required=True,
        max_length=100
    )
    
    starting_rank = discord.ui.TextInput(
        label="Starting Rank",
        placeholder="e.g., Gold 2, Diamond 1",
        required=True,
        max_length=50
    )
    
    ending_rank = discord.ui.TextInput(
        label="Ending Rank",
        placeholder="e.g., Gold 3, Diamond 1 (or 'Remain')",
        required=True,
        max_length=50
    )
    
    winner = discord.ui.TextInput(
        label="Winner",
        placeholder="Who won the 1v1?",
        required=True,
        max_length=100
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.process_ranked_close(interaction, self)

class CloseObservationModal(discord.ui.Modal, title="Close Observation Ticket"):
    observer = discord.ui.TextInput(
        label="Observer Name",
        placeholder="Your name",
        required=True,
        max_length=100
    )
    
    starting_rank = discord.ui.TextInput(
        label="Starting Rank",
        placeholder="e.g., Gold 2, Diamond 1",
        required=True,
        max_length=50
    )
    
    ending_rank = discord.ui.TextInput(
        label="Ending Rank",
        placeholder="e.g., Gold 3, Diamond 1 (or 'Remain')",
        required=True,
        max_length=50
    )
    
    optional_note = discord.ui.TextInput(
        label="Optional Note",
        placeholder="Any additional notes (optional)",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.process_observation_close(interaction, self)

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database(Config.DATABASE_PATH)
        bot.loop.create_task(self.db.init())
    
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"✅ Tickets cog loaded")
        self.bot.add_view(TicketView())
    
    @app_commands.command(name="setup", description="Setup the ticket panel in this channel")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎫 Ticket System",
            description="Click a button below to create a ticket!\n\n"
                       "**🎮 Ranked 1v1** - Request a ranked 1v1 match\n"
                       "**👁️ Personal Observation** - Request a personal observation session",
            color=discord.Color.blue()
        )
        embed.set_footer(text="An observer will assist you shortly after ticket creation")
        
        await interaction.channel.send(embed=embed, view=TicketView())
        await interaction.response.send_message("✅ Ticket panel setup complete!", ephemeral=True)
    
    async def create_ranked_ticket(self, interaction: discord.Interaction, opponent: str):
        """Create a ranked 1v1 ticket"""
        guild = interaction.guild
        user = interaction.user
        
        category = guild.get_channel(Config.TICKET_CATEGORY_ID)
        if not category:
            await interaction.followup.send("❌ Ticket category not configured!", ephemeral=True)
            return
        
        observer_role = guild.get_role(Config.OBSERVER_ROLE_ID)
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if observer_role:
            overwrites[observer_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        channel_name = f"ranked-{user.name}".lower().replace(" ", "-")
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ranked 1v1 | User: {user.id} | Opponent: {opponent}"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create channel: {e}", ephemeral=True)
            return
        
        ticket_id = await self.db.create_ticket(channel.id, user.id, "Ranked 1v1", opponent=opponent)
        
        embed = TicketEmbeds.ticket_created("Ranked 1v1", user, opponent)
        
        observer_mention = observer_role.mention if observer_role else "@Observers"
        await channel.send(
            content=f"{user.mention} {observer_mention}",
            embed=embed
        )
        
        await interaction.followup.send(f"✅ Ticket created! {channel.mention}", ephemeral=True)
    
    async def create_observation_ticket(self, interaction: discord.Interaction, private_link: Optional[str]):
        """Create a personal observation ticket"""
        guild = interaction.guild
        user = interaction.user
        
        category = guild.get_channel(Config.TICKET_CATEGORY_ID)
        if not category:
            await interaction.followup.send("❌ Ticket category not configured!", ephemeral=True)
            return
        
        observer_role = guild.get_role(Config.OBSERVER_ROLE_ID)
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if observer_role:
            overwrites[observer_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        channel_name = f"obs-{user.name}".lower().replace(" ", "-")
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Personal Observation | User: {user.id}"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create channel: {e}", ephemeral=True)
            return
        
        ticket_id = await self.db.create_ticket(channel.id, user.id, "Personal Observation", private_link=private_link)
        
        embed = TicketEmbeds.ticket_created("Personal Observation", user)
        if private_link:
            embed.add_field(name="Private Server Link", value=private_link, inline=False)
        
        observer_mention = observer_role.mention if observer_role else "@Observers"
        await channel.send(
            content=f"{user.mention} {observer_mention}",
            embed=embed
        )
        
        await interaction.followup.send(f"✅ Ticket created! {channel.mention}", ephemeral=True)
    
    @app_commands.command(name="close", description="Close the current ticket")
    async def close(self, interaction: discord.Interaction):
        """Close a ticket - only works in ticket channels"""
        if not interaction.channel.name.startswith(("ranked-", "obs-")):
            await interaction.response.send_message("❌ This command can only be used in ticket channels!", ephemeral=True)
            return
        
        ticket_data = await self.db.get_ticket_by_channel(interaction.channel.id)
        if not ticket_data:
            await interaction.response.send_message("❌ Ticket not found in database!", ephemeral=True)
            return
        
        observer_role = interaction.guild.get_role(Config.OBSERVER_ROLE_ID)
        is_observer = observer_role in interaction.user.roles if observer_role else False
        is_owner = interaction.user.id == ticket_data['user_id']
        
        if not (is_observer or is_owner):
            await interaction.response.send_message("❌ You don't have permission to close this ticket!", ephemeral=True)
            return
        
        if ticket_data['ticket_type'] == "Ranked 1v1":
            modal = CloseRankedModal()
        else:
            modal = CloseObservationModal()
        
        await interaction.response.send_modal(modal)
    
    async def process_ranked_close(self, interaction: discord.Interaction, modal: CloseRankedModal):
        """Process closing a ranked ticket"""
        ticket_data = await self.db.get_ticket_by_channel(interaction.channel.id)
        if not ticket_data:
            await interaction.followup.send("❌ Ticket not found!", ephemeral=True)
            return
        
        await self.db.add_ranked_result(
            ticket_data['id'],
            interaction.user.id,
            modal.observer.value,
            modal.starting_rank.value,
            modal.ending_rank.value,
            modal.winner.value
        )
        
        await self.db.close_ticket(interaction.channel.id, interaction.user.id)
        
        log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
        if log_channel:
            user = await self.bot.fetch_user(ticket_data['user_id'])
            result_data = {
                'observer_name': modal.observer.value,
                'starting_rank': modal.starting_rank.value,
                'ending_rank': modal.ending_rank.value,
                'winner': modal.winner.value
            }
            embed = TicketEmbeds.ticket_log(ticket_data, result_data, user)
            await log_channel.send(embed=embed)
        
        await interaction.followup.send("✅ Ticket closed! Channel will be deleted in 5 seconds...", ephemeral=True)
        await asyncio.sleep(5)
        await interaction.channel.delete()
    
    async def process_observation_close(self, interaction: discord.Interaction, modal: CloseObservationModal):
        """Process closing an observation ticket"""
        ticket_data = await self.db.get_ticket_by_channel(interaction.channel.id)
        if not ticket_data:
            await interaction.followup.send("❌ Ticket not found!", ephemeral=True)
            return
        
        await self.db.add_observation_result(
            ticket_data['id'],
            interaction.user.id,
            modal.observer.value,
            modal.starting_rank.value,
            modal.ending_rank.value,
            modal.optional_note.value if modal.optional_note.value else None
        )
        
        await self.db.close_ticket(interaction.channel.id, interaction.user.id)
        
        log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
        if log_channel:
            user = await self.bot.fetch_user(ticket_data['user_id'])
            result_data = {
                'observer_name': modal.observer.value,
                'starting_rank': modal.starting_rank.value,
                'ending_rank': modal.ending_rank.value,
                'optional_note': modal.optional_note.value if modal.optional_note.value else None
            }
            embed = TicketEmbeds.ticket_log(ticket_data, result_data, user)
            await log_channel.send(embed=embed)
        
        await interaction.followup.send("✅ Ticket closed! Channel will be deleted in 5 seconds...", ephemeral=True)
        await asyncio.sleep(5)
        await interaction.channel.delete()

async def setup(bot):
    await bot.add_cog(Tickets(bot))