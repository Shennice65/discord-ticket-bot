import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import os
import re
from datetime import datetime
from typing import Optional, List

from config import Config
from database import Database
from utils.embeds import TicketEmbeds


from utils.ticket_utils import validate_and_format_rank


class UnrankConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
    
    @discord.ui.button(label="Yes, Unrank Me", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        db = Database()
        await db.init()
        
        success, result = await db.unrank_player(interaction.user.id)
        if success:
            # Strip all tier roles since player is now unranked
            from utils.role_manager import update_tier_role
            await update_tier_role(interaction.guild, interaction.user, "")
            
            await interaction.edit_original_response(
                content=f"You have been unranked. Your previous rank was **{result}**.\n\n"
                        f"**Warning:** You cannot be re-ranked for **1 month**.\n"
                        f"You also cannot request R1s until you are ranked back to **{result}** or higher.",
                view=None
            )
        else:
            await interaction.edit_original_response(content=f"Could not unrank: {result}", view=None)
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Unrank cancelled.", view=None)


class ObservationConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
    
    @discord.ui.button(label="Yes, Request Observation", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Processing...", view=None)
        try:
            cog = interaction.client.get_cog('Tickets')
            if not cog:
                await interaction.edit_original_response(content="❌ Error: Ticket system cog is currently unavailable. Please try again later.", view=None)
                return
            await cog.create_observation_ticket(interaction)
        except Exception as e:
            print(f"Error in ObservationConfirmView: {e}")
            try:
                await interaction.edit_original_response(content=f"❌ An error occurred while creating ticket: {e}", view=None)
            except Exception:
                pass
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Personal Observation request cancelled.", view=None)

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Ranked 1v1", style=discord.ButtonStyle.primary, custom_id="ranked_1v1")
    async def ranked_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = OpponentSelectView()
        await interaction.response.send_message(
            "**Select your opponent from the dropdown below:**\n"
            "*Start typing to search for a user in this server.*",
            view=view,
            ephemeral=True
        )
    
    @discord.ui.button(label="Personal Observation", style=discord.ButtonStyle.secondary, custom_id="personal_obs")
    async def obs_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ObservationConfirmView()
        await interaction.response.send_message(
            "**Are you sure you want to request a Personal Observation?**\n\n"
            "This will notify observers to review your gameplay.\n"
            "You can only request this **once every two weeks**.",
            view=view,
            ephemeral=True
        )
    
    @discord.ui.button(label="Unrank", style=discord.ButtonStyle.danger, custom_id="unrank_self")
    async def unrank_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = UnrankConfirmView()
        await interaction.response.send_message(
            "**Are you sure you want to unrank yourself?**\n\n"
            "This will remove you from the leaderboard entirely.\n"
            "You will **not** be able to get re-ranked for **1 month**.\n"
            "You will **not** be able to request R1s until you reach your original rank again.",
            view=view,
            ephemeral=True
        )


class OpponentSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="Search and select an opponent...",
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        selected_user = self.values[0]
        await interaction.response.edit_message(content=f"Opponent {selected_user.mention} selected. Creating ticket...", view=None)
        try:
            cog = interaction.client.get_cog('Tickets')
            if not cog:
                await interaction.edit_original_response(content="❌ Error: Ticket system cog is currently unavailable. Please try again later.", view=None)
                return
            await cog.create_ranked_ticket(interaction, selected_user)
        except Exception as e:
            print(f"Error in OpponentSelect: {e}")
            try:
                await interaction.edit_original_response(content=f"❌ An error occurred while creating ticket: {e}", view=None)
            except Exception:
                pass


class OpponentSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(OpponentSelect())


class OutOfRangeAcceptView(discord.ui.View):
    def __init__(self, requester: discord.Member, opponent: discord.Member, 
                 channel: discord.TextChannel, cog):
        super().__init__(timeout=300)
        self.requester = requester
        self.opponent = opponent
        self.channel = channel
        self.cog = cog
        
        self.msg = None
        self.responded = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Only the challenged player can respond!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.responded:
            return
        self.responded = True
        self.stop()

        await interaction.response.edit_message(
            content=f"{self.opponent.mention} **accepted** the out-of-range challenge!",
            view=None
        )

        await self.cog._finalize_out_of_range_ticket(
            self.channel, self.requester, self.opponent
        )

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.responded:
            return
        self.responded = True
        self.stop()

        await interaction.response.edit_message(
            content=f"{self.opponent.mention} **declined** the challenge. This channel will be deleted in 10 seconds.",
            view=None
        )

        await self.cog.db.reset_ranked_cooldown_only(self.requester.id)

        await asyncio.sleep(10)
        try:
            await self.channel.delete()
        except discord.errors.NotFound:
            pass

    async def on_timeout(self):
        if self.responded:
            return
        self.responded = True

        try:
            await self.channel.send(
                f"The out-of-range challenge from {self.requester.mention} to {self.opponent.mention} has **expired** (5 min timeout). "
                f"This channel will be deleted in 10 seconds."
            )
        except Exception:
            pass

        await self.cog.db.reset_ranked_cooldown_only(self.requester.id)

        await asyncio.sleep(10)
        try:
            await self.channel.delete()
        except discord.errors.NotFound:
            pass


class WinnerButtonView(discord.ui.View):
    def __init__(self, player1_id: int, player1_name: str, player2_id: int, player2_name: str):
        super().__init__(timeout=120)
        self.player1_id = player1_id
        self.player1_name = player1_name
        self.player2_id = player2_id
        self.player2_name = player2_name
        
        btn1 = discord.ui.Button(label=f"{player1_name}", style=discord.ButtonStyle.primary)
        btn1.callback = self.select_player1
        self.add_item(btn1)
        
        btn2 = discord.ui.Button(label=f"{player2_name}", style=discord.ButtonStyle.primary)
        btn2.callback = self.select_player2
        self.add_item(btn2)
        
        btn3 = discord.ui.Button(label="Cancel Match", style=discord.ButtonStyle.danger)
        btn3.callback = self.cancel_match
        self.add_item(btn3)
    
    async def select_player1(self, interaction: discord.Interaction):
        modal = CloseRankedModal(winner_name=self.player1_name, winner_id=self.player1_id)
        await interaction.response.send_modal(modal)
    
    async def select_player2(self, interaction: discord.Interaction):
        modal = CloseRankedModal(winner_name=self.player2_name, winner_id=self.player2_id)
        await interaction.response.send_modal(modal)

    async def cancel_match(self, interaction: discord.Interaction):
        modal = CloseRankedCancelModal()
        await interaction.response.send_modal(modal)


class CloseRankedModal(discord.ui.Modal):
    def __init__(self, winner_name: str, winner_id: int):
        super().__init__(title="Close Ranked 1v1 Ticket")
        self.winner_name = winner_name
        self.winner_id = winner_id
        
        self.note = discord.ui.TextInput(
            label="Closing Note (Optional)",
            placeholder="Any additional notes about this match...",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.note)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.process_ranked_close(interaction, self)


class CloseRankedCancelModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Cancel Ranked 1v1 Ticket")
        
        self.reason = discord.ui.TextInput(
            label="Reason for Cancellation",
            placeholder="e.g., Opponent didn't show up, dodged, mutual cancel...",
            required=True,
            max_length=500,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.reason)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.process_ranked_cancel(interaction, self)


class CloseObservationModal(discord.ui.Modal):
    def __init__(self, current_rank: str = ""):
        super().__init__(title="Close Observation Ticket")
        
        self.ending_rank = discord.ui.TextInput(
            label="Ending Rank",
            placeholder="e.g., Legends 14 or Unranked (NO 'Remain')",
            required=True,
            max_length=50
        )
        self.add_item(self.ending_rank)
        
        self.note = discord.ui.TextInput(
            label="Closing Note (Optional)",
            placeholder="Any additional notes about this observation...",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.note)
    
    async def on_submit(self, interaction: discord.Interaction):
        end_val = validate_and_format_rank(self.ending_rank.value)
        
        if not end_val:
            await interaction.response.send_message(
                "Invalid Rank Format!\n"
                "Ranks must be exactly one of the official tiers followed by a number.\n"
                "Valid tiers: *Novices, Masters, Legends, Elites, Champions, Phantoms*\n"
                "Example: `Legends 12`",
                ephemeral=True
            )
            return
            
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.process_observation_close(interaction, self, end_val)


