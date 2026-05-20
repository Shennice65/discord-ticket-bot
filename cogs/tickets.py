import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime
from typing import Optional, List

from config import Config
from database import Database
from utils.embeds import TicketEmbeds

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Ranked 1v1", style=discord.ButtonStyle.primary, custom_id="ranked_1v1")
    async def ranked_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RankedModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Personal Observation", style=discord.ButtonStyle.secondary, custom_id="personal_obs")
    async def obs_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.create_observation_ticket(interaction)


class RankedModal(discord.ui.Modal, title="Ranked 1v1 - Private Server Link"):
    private_link = discord.ui.TextInput(
        label="Private Server Link (Optional)",
        placeholder="Enter private server link if applicable",
        required=False,
        max_length=200
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        view = OpponentSelectView(self.private_link.value if self.private_link.value else None)
        await interaction.response.send_message(
            "**Select your opponent from the dropdown below:**\n"
            "*Start typing to search for a user in this server.*",
            view=view,
            ephemeral=True
        )


class OpponentSelect(discord.ui.UserSelect):
    def __init__(self, private_link: Optional[str] = None):
        super().__init__(
            placeholder="Search and select an opponent...",
            min_values=1,
            max_values=1
        )
        self.private_link = private_link
    
    async def callback(self, interaction: discord.Interaction):
        selected_user = self.values[0]
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.create_ranked_ticket(interaction, selected_user, self.private_link)


class OpponentSelectView(discord.ui.View):
    def __init__(self, private_link: Optional[str] = None):
        super().__init__(timeout=120)
        self.add_item(OpponentSelect(private_link))


class CloseRankedModal(discord.ui.Modal, title="Close Ranked 1v1 Ticket"):
    observer = discord.ui.TextInput(
        label="Observer Name",
        placeholder="Your name",
        required=True,
        max_length=100
    )
    
    starting_rank = discord.ui.TextInput(
        label="Starting Rank",
        placeholder="e.g., Legends 12, Champions 9, Legends 5",
        required=True,
        max_length=50
    )
    
    ending_rank = discord.ui.TextInput(
        label="Ending Rank",
        placeholder="e.g., Legends 14, Champions 10 (or 'Remain')",
        required=True,
        max_length=50
    )
    
    winner = discord.ui.TextInput(
        label="Winner",
        placeholder="Who won the 1v1?",
        required=True,
        max_length=100
    )
    
    note = discord.ui.TextInput(
        label="Closing Note (Optional)",
        placeholder="Any additional notes about this match...",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph
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
        placeholder="e.g., Legends 12, Champions 9, Legends 5",
        required=True,
        max_length=50
    )
    
    ending_rank = discord.ui.TextInput(
        label="Ending Rank",
        placeholder="e.g., Legends 14, Champions 10 (or 'Remain')",
        required=True,
        max_length=50
    )
    
    note = discord.ui.TextInput(
        label="Closing Note (Optional)",
        placeholder="Any additional notes about this observation...",
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
        print(f"Tickets cog loaded")
        self.bot.add_view(TicketView())
    
    @app_commands.command(name="setup", description="Setup the ticket panel in this channel")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Ticket System",
            description="Click a button below to create a ticket!\n\n"
                       "**Ranked 1v1** - Request a ranked 1v1 match\n"
                       "**Personal Observation** - Request a personal observation session",
            color=discord.Color.blue()
        )
        embed.set_footer(text="An observer will assist you shortly after ticket creation")
        
        await interaction.channel.send(embed=embed, view=TicketView())
        await interaction.response.send_message("Ticket panel setup complete!", ephemeral=True)
    
    @commands.command(name="forcesetup")
    @commands.has_permissions(administrator=True)
    async def force_setup(self, ctx):
        """Force create the ticket panel"""
        embed = discord.Embed(
            title="Ticket System",
            description="Click a button below to create a ticket!\n\n"
                       "**Ranked 1v1** - Request a ranked 1v1 match\n"
                       "**Personal Observation** - Request a personal observation session",
            color=discord.Color.blue()
        )
        embed.set_footer(text="An observer will assist you shortly after ticket creation")
        
        view = TicketView()
        await ctx.send(embed=embed, view=view)
    
    async def create_ranked_ticket(self, interaction: discord.Interaction, opponent: discord.User, private_link: Optional[str]):
        """Create a ranked 1v1 ticket"""
        guild = interaction.guild
        user = interaction.user
        
        opponent_member = guild.get_member(opponent.id)
        if not opponent_member:
            await interaction.followup.send("Could not find that user in this server!", ephemeral=True)
            return
        
        if opponent_member.id == user.id:
            await interaction.followup.send("You cannot 1v1 yourself!", ephemeral=True)
            return
        
        category = guild.get_channel(Config.TICKET_CATEGORY_ID)
        if not category:
            await interaction.followup.send("Ticket category not configured!", ephemeral=True)
            return
        
        observer_role = guild.get_role(Config.OBSERVER_ROLE_ID)
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            opponent_member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if observer_role:
            overwrites[observer_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        channel_name = f"ranked-{user.name}-vs-{opponent.name}".lower().replace(" ", "-")[:100]
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ranked 1v1 | {user.name} vs {opponent.name}"
            )
        except Exception as e:
            await interaction.followup.send(f"Failed to create channel: {e}", ephemeral=True)
            return
        
        ticket_id = await self.db.create_ticket(
            channel.id, 
            user.id, 
            "Ranked 1v1", 
            opponent=opponent.name, 
            private_link=private_link
        )
        
        embed = TicketEmbeds.ticket_created("Ranked 1v1", user, opponent.name)
        if private_link:
            embed.add_field(name="Private Server Link", value=private_link, inline=False)
        
        observer_mention = observer_role.mention if observer_role else "@Observers"
        await channel.send(
            content=f"{user.mention} {opponent_member.mention} {observer_mention}",
            embed=embed
        )
        
        await interaction.edit_original_response(
            content=f"Ticket created! {channel.mention}",
            view=None
        )
    
    async def create_observation_ticket(self, interaction: discord.Interaction):
        """Create a personal observation ticket"""
        guild = interaction.guild
        user = interaction.user
        
        category = guild.get_channel(Config.TICKET_CATEGORY_ID)
        if not category:
            await interaction.followup.send("Ticket category not configured!", ephemeral=True)
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
                topic=f"Personal Observation | User: {user.name}"
            )
        except Exception as e:
            await interaction.followup.send(f"Failed to create channel: {e}", ephemeral=True)
            return
        
        ticket_id = await self.db.create_ticket(channel.id, user.id, "Personal Observation")
        
        embed = TicketEmbeds.ticket_created("Personal Observation", user)
        
        observer_mention = observer_role.mention if observer_role else "@Observers"
        await channel.send(
            content=f"{user.mention} {observer_mention}",
            embed=embed
        )
        
        await interaction.followup.send(f"Ticket created! {channel.mention}", ephemeral=True)
    
    @app_commands.command(name="close", description="Close the current ticket")
    async def close(self, interaction: discord.Interaction):
        if not interaction.channel.name.startswith(("ranked-", "obs-")):
            await interaction.response.send_message("This command can only be used in ticket channels!", ephemeral=True)
            return
        
        ticket_data = await self.db.get_ticket_by_channel(interaction.channel.id)
        if not ticket_data:
            await interaction.response.send_message("Ticket not found in database!", ephemeral=True)
            return
        
        observer_role = interaction.guild.get_role(Config.OBSERVER_ROLE_ID)
        is_observer = observer_role in interaction.user.roles if observer_role else False
        is_owner = interaction.user.id == ticket_data['user_id']
        
        if not (is_observer or is_owner):
            await interaction.response.send_message("You don't have permission to close this ticket!", ephemeral=True)
            return
        
        if ticket_data['ticket_type'] == "Ranked 1v1":
            modal = CloseRankedModal()
        else:
            modal = CloseObservationModal()
        
        await interaction.response.send_modal(modal)
    
    async def process_ranked_close(self, interaction: discord.Interaction, modal: CloseRankedModal):
        ticket_data = await self.db.get_ticket_by_channel(interaction.channel.id)
        if not ticket_data:
            await interaction.followup.send("Ticket not found!", ephemeral=True)
            return
        
        await self.db.add_ranked_result(
            ticket_data['id'],
            interaction.user.id,
            modal.observer.value,
            modal.starting_rank.value,
            modal.ending_rank.value,
            modal.winner.value,
            modal.note.value if modal.note.value else None
        )
        
        await self.db.close_ticket(interaction.channel.id, interaction.user.id)
        
        log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
        if log_channel:
            user = await self.bot.fetch_user(ticket_data['user_id'])
            result_data = {
                'observer_name': modal.observer.value,
                'starting_rank': modal.starting_rank.value,
                'ending_rank': modal.ending_rank.value,
                'winner': modal.winner.value,
                'note': modal.note.value if modal.note.value else None
            }
            embed = TicketEmbeds.ticket_log(ticket_data, result_data, user)
            await log_channel.send(embed=embed)
        
        await interaction.followup.send("Ticket closed! Channel will be deleted in 5 seconds...", ephemeral=True)
        await asyncio.sleep(5)
        await interaction.channel.delete()
    
    async def process_observation_close(self, interaction: discord.Interaction, modal: CloseObservationModal):
        ticket_data = await self.db.get_ticket_by_channel(interaction.channel.id)
        if not ticket_data:
            await interaction.followup.send("Ticket not found!", ephemeral=True)
            return
        
        await self.db.add_observation_result(
            ticket_data['id'],
            interaction.user.id,
            modal.observer.value,
            modal.starting_rank.value,
            modal.ending_rank.value,
            modal.note.value if modal.note.value else None
        )
        
        await self.db.close_ticket(interaction.channel.id, interaction.user.id)
        
        log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
        if log_channel:
            user = await self.bot.fetch_user(ticket_data['user_id'])
            result_data = {
                'observer_name': modal.observer.value,
                'starting_rank': modal.starting_rank.value,
                'ending_rank': modal.ending_rank.value,
                'note': modal.note.value if modal.note.value else None
            }
            embed = TicketEmbeds.ticket_log(ticket_data, result_data, user)
            await log_channel.send(embed=embed)
        
        await interaction.followup.send("Ticket closed! Channel will be deleted in 5 seconds...", ephemeral=True)
        await asyncio.sleep(5)
        await interaction.channel.delete()


async def setup(bot):
    await bot.add_cog(Tickets(bot))