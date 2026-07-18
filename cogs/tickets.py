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

def validate_and_format_rank(rank_str: str) -> Optional[str]:
    tiers = {
        "novice": "Novices", "novices": "Novices",
        "master": "Masters", "masters": "Masters",
        "legend": "Legends", "legends": "Legends",
        "elite": "Elites", "elites": "Elites",
        "champion": "Champions", "champions": "Champions",
        "phantom": "Phantoms", "phantoms": "Phantoms"
    }
    match = re.match(r'^\s*([a-zA-Z]+)\s*(\d+)\s*$', rank_str)
    if not match:
        return None
    tier_input = match.group(1).lower()
    number = match.group(2)
    if tier_input in tiers:
        return f"{tiers[tier_input]} {number}"
    return None

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
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content="Processing...", view=None)
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.create_observation_ticket(interaction)
    
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
        
        cog = interaction.client.get_cog('Tickets')
        if cog:
            await cog.create_ranked_ticket(interaction, selected_user)


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
            placeholder="e.g., Legends 14 (NO 'Remain' or 'Same')",
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


def get_observer_mention(guild: discord.Guild) -> str:
    """Get combined mention string for both Observer and Trial Observer roles."""
    mentions = []
    observer_role = guild.get_role(Config.OBSERVER_ROLE_ID)
    if observer_role:
        mentions.append(observer_role.mention)
    if hasattr(Config, 'TRIAL_OBSERVER_ROLE_ID') and Config.TRIAL_OBSERVER_ROLE_ID:
        trial_role = guild.get_role(Config.TRIAL_OBSERVER_ROLE_ID)
        if trial_role:
            mentions.append(trial_role.mention)
    if not mentions:
        mentions.append("@Observers")
    return " ".join(mentions)


def is_observer_or_trial(member: discord.Member) -> bool:
    """Check if a member has Observer or Trial Observer role."""
    observer_role = member.guild.get_role(Config.OBSERVER_ROLE_ID)
    if observer_role and observer_role in member.roles:
        return True
    if hasattr(Config, 'TRIAL_OBSERVER_ROLE_ID') and Config.TRIAL_OBSERVER_ROLE_ID:
        trial_role = member.guild.get_role(Config.TRIAL_OBSERVER_ROLE_ID)
        if trial_role and trial_role in member.roles:
            return True
    return False


def get_observer_overwrites(guild: discord.Guild, base_overwrites: dict) -> dict:
    """Add Observer and Trial Observer role overwrites to a permission dict."""
    overwrites = base_overwrites.copy()
    observer_role = guild.get_role(Config.OBSERVER_ROLE_ID)
    if observer_role:
        overwrites[observer_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    if hasattr(Config, 'TRIAL_OBSERVER_ROLE_ID') and Config.TRIAL_OBSERVER_ROLE_ID:
        trial_role = guild.get_role(Config.TRIAL_OBSERVER_ROLE_ID)
        if trial_role:
            overwrites[trial_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    return overwrites


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.db_ready = True
        bot.loop.create_task(self.db.init())
        self.cleanup_stale_tickets.start()
        
    def cog_unload(self):
        self.cleanup_stale_tickets.cancel()

    @tasks.loop(hours=1)
    async def cleanup_stale_tickets(self):
        await self.bot.wait_until_ready()
        if self.db.tickets is None:
            return
            
        now_naive = datetime.utcnow()
        cursor = self.db.tickets.find({"status": "open", "ticket_type": "Ranked 1v1", "ducking_ping_sent": {"$ne": True}})
        open_tickets = await cursor.to_list(length=None)
        
        for ticket in open_tickets:
            channel = self.bot.get_channel(ticket['channel_id'])
            
            if channel:
                try:
                    try:
                        created = datetime.fromisoformat(ticket['created_at'])
                        if (now_naive - created).total_seconds() > 604800:
                            observer_mention = get_observer_mention(channel.guild)
                            await channel.send(f"{observer_mention} This ticket has been inactive for 7 days. Please check if the requested player is avoiding the match.")
                            await self.db.mark_ducking_ping_sent(ticket['channel_id'])
                    except ValueError:
                        pass
                except Exception as e:
                    print(f"Cleanup error on {channel.id}: {e}")
    
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Tickets cog loaded")
        self.bot.add_view(TicketView())
    
    @app_commands.command(name="dbcheck", description="Check database status (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def db_check(self, interaction: discord.Interaction):
        file_exists = os.path.exists('bot_data.json')
        await interaction.response.send_message(
            f"DB Ready: {self.db_ready}\nData file exists: {file_exists}",
            ephemeral=True
        )
    
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
    
    @app_commands.command(name="updateperms", description="Add a role's permissions to all existing ticket channels")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="The role to add to all ticket channels")
    async def update_perms(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        category = guild.get_channel(Config.TICKET_CATEGORY_ID)
        
        if not category:
            await interaction.followup.send("Ticket category not found!", ephemeral=True)
            return
        
        updated = 0
        skipped = 0
        
        for channel in category.channels:
            if channel.name.startswith(("ranked-", "obs-")):
                current_perms = channel.overwrites_for(role)
                if current_perms.read_messages:
                    skipped += 1
                else:
                    try:
                        await channel.set_permissions(role, read_messages=True, send_messages=True)
                        updated += 1
                    except Exception as e:
                        print(f"Failed to update {channel.name}: {e}")
        
        await interaction.followup.send(
            f"Done! Updated {updated} channels with {role.mention} permissions.\n"
            f"{skipped} channels already had permissions.",
            ephemeral=True
        )
    
    async def create_ranked_ticket(self, interaction: discord.Interaction, opponent: discord.User):
        guild = interaction.guild
        user = interaction.user
        
        opponent_member = guild.get_member(opponent.id)
        if not opponent_member:
            await interaction.followup.send("Could not find that user in this server!", ephemeral=True)
            return
            
        existing_ticket = await self.db.tickets.find_one({"user_id": user.id, "status": "open"})
        if existing_ticket:
            existing_channel = guild.get_channel(existing_ticket["channel_id"])
            if not existing_channel:
                await self.db.close_ticket(existing_ticket["channel_id"], self.bot.user.id)
            else:
                await interaction.followup.send(f"You already have an open ticket in {existing_channel.mention}! Please close it before opening a new one.", ephemeral=True)
                return
        
        if opponent_member.id == user.id and not opponent_member.bot:
            await interaction.followup.send("You cannot 1v1 yourself!", ephemeral=True)
            return
        
        can_r1, r1_reason = await self.db.can_player_r1(user.id)
        if not can_r1:
            await interaction.followup.send(r1_reason, ephemeral=True)
            return
        
        idx_user = await self.db.get_global_rank_index(user.id)
        idx_opp = await self.db.get_global_rank_index(opponent.id)
        
        is_out_of_range = False
        if idx_user != -1 and idx_opp != -1:
            if abs(idx_user - idx_opp) > 5:
                is_out_of_range = True
                
        cooldown = await self.db.get_ranked_cooldown(user.id)
        if cooldown > 0:
            hours = int(cooldown)
            minutes = int((cooldown - hours) * 60)
            await interaction.followup.send(f"You can only request one ranked match per day! Please wait **{hours}h {minutes}m**.", ephemeral=True)
            return
        
        rematch_cd = await self.db.get_rematch_cooldown(user.id, opponent.id)
        if rematch_cd > 0:
            hours = int(rematch_cd)
            minutes = int((rematch_cd - hours) * 60)
            await interaction.followup.send(f"You must wait **{hours}h {minutes}m** before facing {opponent_member.mention} again!", ephemeral=True)
            return
        
        category = guild.get_channel(Config.TICKET_CATEGORY_ID)
        if not category:
            await interaction.followup.send("Ticket category not configured!", ephemeral=True)
            return
        
        observer_mention = get_observer_mention(guild)
        
        overwrites = get_observer_overwrites(guild, {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            opponent_member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        })
        
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
        
        if is_out_of_range:
            await self.db.update_ranked_cooldown(user.id)
            
            user_rank = await self.db.get_player_rank(user.id)
            opp_rank = await self.db.get_player_rank(opponent.id)
            
            embed = discord.Embed(
                title="Out-of-Range Challenge",
                description=(
                    f"{user.mention} wants to challenge {opponent_member.mention} to a **Ranked 1v1**!\n\n"
                    f"**{user.display_name}** is ranked **{user_rank or 'Unranked'}**\n"
                    f"**{opponent_member.display_name}** is ranked **{opp_rank or 'Unranked'}**\n\n"
                    f"This match is **outside the 5-rank window**.\n"
                    f"{opponent_member.mention}, do you accept this challenge?"
                ),
                color=discord.Color.orange()
            )
            embed.set_footer(text="This request expires in 5 minutes.")
            
            view = OutOfRangeAcceptView(user, opponent_member, channel, self)
            await channel.send(
                content=f"{user.mention} {opponent_member.mention}",
                embed=embed,
                view=view
            )
            
            await interaction.edit_original_response(
                content=f"Out-of-range challenge sent! Waiting for {opponent_member.mention} to accept in {channel.mention}.",
                view=None
            )
            return
        
        ticket_id = await self.db.create_ranked_ticket_db(
            channel.id, user.id, 
            opponent_name=opponent.name, opponent_id=opponent.id
        )
        print(f"Ticket {ticket_id} saved")
        
        await self.db.update_ranked_cooldown(user.id)
        
        user_history = await self.db.get_user_history(user.id, user.name)
        opp_history = await self.db.get_user_history(opponent.id, opponent.name)
        
        u_matches, u_wins, u_losses, u_rate = TicketEmbeds.calculate_ranked_stats(user.id, user.name, user_history)
        o_matches, o_wins, o_losses, o_rate = TicketEmbeds.calculate_ranked_stats(opponent.id, opponent.name, opp_history)
        
        u_rank = await self.db.get_player_rank(user.id) or "Unranked"
        o_rank = await self.db.get_player_rank(opponent.id) or "Unranked"
        
        user_stats = f"**Rank**: `{u_rank}`\n**Total Matches**: `{u_matches}`\n**Win Rate**: `{u_rate:.1f}%`"
        opp_stats = f"**Rank**: `{o_rank}`\n**Total Matches**: `{o_matches}`\n**Win Rate**: `{o_rate:.1f}%`"
        
        embed = TicketEmbeds.ticket_created(
            "Ranked 1v1", user, opponent.name,
            user_stats=user_stats, opp_stats=opp_stats
        )
        
        await channel.send(
            content=f"{user.mention} {opponent_member.mention} {observer_mention}",
            embed=embed
        )
        
        await interaction.edit_original_response(
            content=f"Ticket created! {channel.mention}",
            view=None
        )
    
    async def _finalize_out_of_range_ticket(self, channel: discord.TextChannel, 
                                             requester: discord.Member, opponent: discord.Member):
        ticket_id = await self.db.create_ranked_ticket_db(
            channel.id, requester.id,
            opponent_name=opponent.name, opponent_id=opponent.id,
            out_of_range=True
        )
        print(f"Out-of-range ticket {ticket_id} saved")
        
        observer_mention = get_observer_mention(channel.guild)
        
        user_history = await self.db.get_user_history(requester.id, requester.name)
        opp_history = await self.db.get_user_history(opponent.id, opponent.name)
        
        u_matches, u_wins, u_losses, u_rate = TicketEmbeds.calculate_ranked_stats(requester.id, requester.name, user_history)
        o_matches, o_wins, o_losses, o_rate = TicketEmbeds.calculate_ranked_stats(opponent.id, opponent.name, opp_history)
        
        u_rank = await self.db.get_player_rank(requester.id) or "Unranked"
        o_rank = await self.db.get_player_rank(opponent.id) or "Unranked"
        
        user_stats = f"**Rank**: `{u_rank}`\n**Total Matches**: `{u_matches}`\n**Win Rate**: `{u_rate:.1f}%`"
        opp_stats = f"**Rank**: `{o_rank}`\n**Total Matches**: `{o_matches}`\n**Win Rate**: `{o_rate:.1f}%`"
        
        embed = TicketEmbeds.ticket_created(
            "Ranked 1v1", requester, opponent.name,
            user_stats=user_stats, opp_stats=opp_stats
        )
        embed.add_field(
            name="Out-of-Range Match",
            value="This match was accepted outside the 5-rank window.",
            inline=False
        )

        await channel.send(
            content=f"{requester.mention} {opponent.mention} {observer_mention}",
            embed=embed
        )
    
    async def create_observation_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        
        existing_ticket = await self.db.tickets.find_one({"user_id": user.id, "status": "open"})
        if existing_ticket:
            existing_channel = guild.get_channel(existing_ticket["channel_id"])
            if not existing_channel:
                await self.db.close_ticket(existing_ticket["channel_id"], self.bot.user.id)
            else:
                await interaction.followup.send(f"You already have an open ticket in {existing_channel.mention}! Please close it before opening a new one.", ephemeral=True)
                return
            
        cooldown = await self.db.get_obs_cooldown(user.id)
        if cooldown > 0:
            days = int(cooldown)
            hours = int((cooldown - days) * 24)
            await interaction.followup.send(f"You can only request a personal observation once every two weeks! Please wait **{days}d {hours}h**.", ephemeral=True)
            return
            
        category = guild.get_channel(Config.TICKET_CATEGORY_ID)
        if not category:
            await interaction.followup.send("Ticket category not configured!", ephemeral=True)
            return
        
        observer_mention = get_observer_mention(guild)
        
        overwrites = get_observer_overwrites(guild, {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        })
        
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
        print(f"Ticket {ticket_id} saved")
        
        await self.db.update_obs_cooldown(user.id)
        
        user_history = await self.db.get_user_history(user.id, user.name)
        total_obs = len(user_history.get('observations', []))
        u_rank = await self.db.get_player_rank(user.id) or "Unranked"
        user_stats = f"**Rank**: `{u_rank}`\n**Total Observations**: `{total_obs}`"
        
        embed = TicketEmbeds.ticket_created(
            "Personal Observation", user, user_stats=user_stats
        )
        
        await channel.send(
            content=f"{user.mention} {observer_mention}",
            embed=embed
        )
        
        await interaction.followup.send(f"Ticket created! {channel.mention}", ephemeral=True)
    
    @app_commands.command(name="clearticket", description="Forcefully close all open tickets for a user in the database")
    @app_commands.default_permissions(administrator=True)
    async def clearticket(self, interaction: discord.Interaction, target: discord.User):
        await interaction.response.defer(ephemeral=True)
        cursor = self.db.tickets.find({"user_id": target.id, "status": "open"})
        open_tickets = await cursor.to_list(length=None)
        
        if not open_tickets:
            await interaction.followup.send(f"{target.mention} has no open tickets in the database.", ephemeral=True)
            return
            
        closed_count = 0
        for ticket in open_tickets:
            await self.db.close_ticket(ticket['channel_id'], interaction.user.id)
            closed_count += 1
            
        await interaction.followup.send(f"Successfully closed {closed_count} open ticket(s) for {target.mention} in the database.", ephemeral=True)

    @app_commands.command(name="close", description="Close the current ticket")
    async def close(self, interaction: discord.Interaction):
        if not interaction.channel.name.startswith(("ranked-", "obs-")):
            await interaction.response.send_message("This command can only be used in ticket channels!", ephemeral=True)
            return
        
        ticket_data = await self.db.get_ticket_by_channel(interaction.channel.id)
        if not ticket_data:
            await interaction.response.send_message("Ticket not found in database!", ephemeral=True)
            return
        
        if ticket_data.get("status") == "processing":
            await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
            ticket_data["status"] = "open"
            
        if ticket_data.get("status") == "closed":
            await interaction.response.send_message("This ticket was already closed in the database. Deleting channel now...", ephemeral=True)
            await asyncio.sleep(3)
            try:
                await interaction.channel.delete()
            except discord.errors.NotFound:
                pass
            return
        
        is_obs = is_observer_or_trial(interaction.user)
        is_owner = interaction.user.id == ticket_data['user_id']
        
        if not is_obs:
            if ticket_data['ticket_type'] == "Ranked 1v1":
                await interaction.response.send_message("Only observers can close Ranked 1v1 tickets!", ephemeral=True)
                return
            elif not is_owner:
                await interaction.response.send_message("You don't have permission to close this ticket!", ephemeral=True)
                return
        
        if is_obs:
            if ticket_data['ticket_type'] == "Ranked 1v1":
                player1_id = ticket_data['user_id']
                player2_id = ticket_data['opponent_id']
                player1 = interaction.guild.get_member(player1_id)
                player2 = interaction.guild.get_member(player2_id)
                p1_name = player1.display_name if player1 else f"User {player1_id}"
                p2_name = player2.display_name if player2 else f"User {player2_id}"
                view = WinnerButtonView(player1_id, p1_name, player2_id, p2_name)
                await interaction.response.send_message(f"**Who won this match?**\n`{p1_name}` vs `{p2_name}`", view=view, ephemeral=True)
            else:
                current_rank = await self.db.get_player_rank(ticket_data['user_id'])
                modal = CloseObservationModal(current_rank=current_rank)
                await interaction.response.send_modal(modal)
        else:
            await interaction.response.defer(ephemeral=True)
            await self.db.close_ticket(interaction.channel.id, interaction.user.id)
            
            log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
            if log_channel:
                user = await self.bot.fetch_user(ticket_data['user_id'])
                embed = discord.Embed(
                    title=f"Ticket Closed by User - {ticket_data['ticket_type']}",
                    description="Ticket was closed by the user without observer results.",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="User", value=f"{user.mention}\n`{user.name}`", inline=True)
                await log_channel.send(embed=embed)
                
            await interaction.followup.send("Ticket closed! Channel will be deleted in 5 seconds...", ephemeral=True)
            await asyncio.sleep(5)
            await interaction.channel.delete()
    
    async def process_ranked_close(self, interaction: discord.Interaction, modal: CloseRankedModal):
        result = await self.db.tickets.find_one_and_update(
            {"channel_id": interaction.channel.id, "status": "open"},
            {"$set": {"status": "processing"}}
        )
        if not result:
            await interaction.followup.send("This ticket has already been closed or is being processed!", ephemeral=True)
            return
        ticket_data = result
        
        try:
            if not ticket_data.get("out_of_range", False):
                idx_user = await self.db.get_global_rank_index(ticket_data['user_id'])
                idx_opp = await self.db.get_global_rank_index(ticket_data['opponent_id'])
                
                if idx_user != -1 and idx_opp != -1:
                    if abs(idx_user - idx_opp) > 5:
                        await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
                        await interaction.followup.send("Match Invalidated! The players are no longer within 5 ranks of each other. Please close this ticket manually without rank changes.", ephemeral=True)
                        return
                    
            winner_id = modal.winner_id
            loser_id = ticket_data['user_id'] if winner_id == ticket_data['opponent_id'] else ticket_data['opponent_id']
            
            old_win, new_win, old_lose, new_lose = await self.db.process_match_result(winner_id, loser_id)
            
            await self.db.add_ranked_result(
                ticket_data['id'],
                interaction.user.id,
                interaction.user.name,
                old_win,
                new_win,
                old_lose,
                new_lose,
                modal.winner_id,
                modal.winner_name,
                modal.note.value if modal.note.value else None
            )
            await self.db.close_ticket(interaction.channel.id, interaction.user.id)
            
            log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
            if log_channel:
                user = await self.bot.fetch_user(ticket_data['user_id'])
                result_data = {
                    'observer_name': interaction.user.name,
                    'winner_old': old_win,
                    'winner_new': new_win,
                    'loser_old': old_lose,
                    'loser_new': new_lose,
                    'winner': modal.winner_name,
                    'note': modal.note.value if modal.note.value else None
                }
                embed = TicketEmbeds.ticket_log(ticket_data, result_data, user)
                await log_channel.send(embed=embed)
            
            await interaction.followup.send("Ticket closed! Channel will be deleted in 5 seconds...", ephemeral=True)
            await asyncio.sleep(5)
            await interaction.channel.delete()
        except Exception as e:
            await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
            await interaction.followup.send(f"An error occurred while closing: {e}\nThe ticket has been unlocked so you can try again.", ephemeral=True)
            print(f"Error in process_ranked_close: {e}")

    async def process_ranked_cancel(self, interaction: discord.Interaction, modal: CloseRankedCancelModal):
        result = await self.db.tickets.find_one_and_update(
            {"channel_id": interaction.channel.id, "status": "open"},
            {"$set": {"status": "processing"}}
        )
        if not result:
            await interaction.followup.send("This ticket has already been closed or is being processed!", ephemeral=True)
            return
        ticket_data = result
        
        try:
            await self.db.close_ticket(interaction.channel.id, interaction.user.id)
            await self.db.reset_ranked_cooldown_only(ticket_data['user_id'])
            
            log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
            if log_channel:
                user = await self.bot.fetch_user(ticket_data['user_id'])
                
                embed = discord.Embed(
                    title=f"Ticket Cancelled - {ticket_data['ticket_type']}",
                    description="The match was cancelled and closed without recording any rank changes.",
                    color=discord.Color.yellow(),
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(name="User", value=f"{user.mention}\n`{user.name}`", inline=True)
                
                if ticket_data.get('opponent_name'):
                    opponent_value = f"<@{ticket_data['opponent_id']}>\n`{ticket_data['opponent_name']}`" if ticket_data.get('opponent_id') else f"`{ticket_data['opponent_name']}`"
                    embed.add_field(name="Opponent", value=opponent_value, inline=True)
                    
                embed.add_field(name="Observer", value=f"`{interaction.user.name}`", inline=True)
                embed.add_field(name="Reason", value=f"{modal.reason.value}", inline=False)
                
                await log_channel.send(embed=embed)
                
            await interaction.followup.send("Match cancelled! Channel will be deleted in 5 seconds...", ephemeral=True)
            await asyncio.sleep(5)
            await interaction.channel.delete()
        except Exception as e:
            await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
            await interaction.followup.send(f"An error occurred while cancelling: {e}\nThe ticket has been unlocked so you can try again.", ephemeral=True)
            print(f"Error in process_ranked_cancel: {e}")
    
    async def process_observation_close(self, interaction: discord.Interaction, modal: CloseObservationModal, end_rank: str):
        result = await self.db.tickets.find_one_and_update(
            {"channel_id": interaction.channel.id, "status": "open"},
            {"$set": {"status": "processing"}}
        )
        if not result:
            await interaction.followup.send("This ticket has already been closed or is being processed!", ephemeral=True)
            return
        ticket_data = result
        
        try:
            user_id = ticket_data['user_id']
            old_rank = await self.db.get_player_rank(user_id)
            
            from ladder_utils import parse_rank
            parsed = parse_rank(end_rank)
            if parsed:
                tier, target_num = parsed
                current_count = await self.db.get_tier_count(tier)
                
                if target_num > current_count + 1:
                    await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
                    await interaction.followup.send(f"Invalid Rank Gap! You cannot place a player at {end_rank} because there are only {current_count} players in {tier}. The maximum rank you can assign is {tier} {current_count + 1}.", ephemeral=True)
                    return
            
            success, actual_new_rank = await self.db.force_set_player_rank(user_id, end_rank)
            
            if not success:
                await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
                await interaction.followup.send("Failed to update rank. Please ensure the rank is formatted correctly.", ephemeral=True)
                return
            
            await self.db.add_observation_result(
                ticket_data['id'],
                interaction.user.id,
                interaction.user.name,
                old_rank if old_rank else "Unranked",
                actual_new_rank,
                modal.note.value if modal.note.value else None
            )
            await self.db.close_ticket(interaction.channel.id, interaction.user.id)
            
            log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
            if log_channel:
                user = await self.bot.fetch_user(ticket_data['user_id'])
                result_data = {
                    'observer_name': interaction.user.name,
                    'starting_rank': old_rank if old_rank else "Unranked",
                    'ending_rank': actual_new_rank,
                    'note': modal.note.value if modal.note.value else None
                }
                embed = TicketEmbeds.ticket_log(ticket_data, result_data, user)
                await log_channel.send(embed=embed)
            
            await interaction.followup.send("Ticket closed! Channel will be deleted in 5 seconds...", ephemeral=True)
            await asyncio.sleep(5)
            await interaction.channel.delete()
        except Exception as e:
            await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
            await interaction.followup.send(f"An error occurred while closing: {e}\nThe ticket has been unlocked so you can try again.", ephemeral=True)
            print(f"Error in process_observation_close: {e}")


async def setup(bot):
    await bot.add_cog(Tickets(bot))