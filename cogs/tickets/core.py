import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import re
from datetime import datetime
from typing import Optional

from config import Config
from database import Database
from utils.embeds import TicketEmbeds
from utils.admin_alerts import check_and_alert_alt_risk


from views.ticket_views import *
from utils.ticket_utils import *


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Tickets cog loaded")
        self.bot.add_view(TicketView())
        self.bot.add_view(OutOfRangeAcceptView())
    
    async def create_ranked_ticket(self, interaction: discord.Interaction, opponent: discord.User):
        guild = interaction.guild
        user = interaction.user
        
        opponent_member = guild.get_member(opponent.id)
        if not opponent_member:
            await interaction.followup.send("Could not find that user in this server!", ephemeral=True)
            return

        # Run private risk checks without delaying ticket creation.
        asyncio.create_task(check_and_alert_alt_risk(self.bot, user))
        asyncio.create_task(check_and_alert_alt_risk(self.bot, opponent_member))
            
        existing_ticket = await self.db.tickets.find_one({"user_id": user.id, "status": "open"})
        if existing_ticket:
            existing_channel = guild.get_channel(existing_ticket["channel_id"])
            if not existing_channel:
                await self.db.close_ticket(existing_ticket["channel_id"], self.bot.user.id)
            else:
                await interaction.followup.send(f"You already have an open ticket in {existing_channel.mention}! Please close it before opening a new one.", ephemeral=True)
                return
        
        ticket_service = self.bot.container.get('TicketService')
        is_valid, error_msg, is_out_of_range = await ticket_service.validate_ranked_request(user.id, opponent.id)
        if not is_valid:
            await interaction.followup.send(error_msg, ephemeral=True)
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
        }, ticket_type="Ranked 1v1")
        
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
            embed.set_footer(text="This request expires in 24 hours.")
            
            ticket_id = await self.db.create_ranked_ticket_db(
                channel.id, user.id, 
                opponent_name=opponent.name, opponent_id=opponent.id,
                out_of_range=True, status="pending_accept"
            )
            print(f"Out-of-range ticket {ticket_id} pending accept")
            
            view = OutOfRangeAcceptView(self)
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
        
        user_history = await self.db.get_user_history(user.id, user.name)
        opp_history = await self.db.get_user_history(opponent.id, opponent.name)
        
        u_matches, u_wins, u_losses, u_rate = TicketEmbeds.calculate_ranked_stats(user.id, user.name, user_history)
        o_matches, o_wins, o_losses, o_rate = TicketEmbeds.calculate_ranked_stats(opponent.id, opponent.name, opp_history)
        
        u_rank = await self.db.get_player_rank(user.id) or "Unranked"
        o_rank = await self.db.get_player_rank(opponent.id) or "Unranked"
        
        embed, tier_file = TicketEmbeds.create_ranked_1v1_ticket_embed(
            user=user,
            opponent_name=opponent.name,
            u_rank=u_rank,
            o_rank=o_rank,
            u_rate=u_rate,
            o_rate=o_rate,
            u_matches=u_matches,
            o_matches=o_matches
        )
        
        send_kwargs = {
            "content": f"{user.mention} {opponent_member.mention} {observer_mention}",
            "embed": embed
        }
        site_view = TicketEmbeds.ranked_site_view()
        if site_view:
            send_kwargs["view"] = site_view
        if tier_file:
            send_kwargs["file"] = tier_file

        await channel.send(**send_kwargs)
        
        await interaction.edit_original_response(
            content=f"Ticket created! {channel.mention}",
            view=None
        )
    
    async def _finalize_out_of_range_ticket(self, channel: discord.TextChannel, 
                                             requester: discord.Member, opponent: discord.Member):
        await self.db.tickets.update_one(
            {"channel_id": channel.id, "status": "pending_accept"},
            {"$set": {"status": "open"}}
        )
        print(f"Out-of-range ticket in {channel.id} finalized and opened")
        
        observer_mention = get_observer_mention(channel.guild)
        
        user_history = await self.db.get_user_history(requester.id, requester.name)
        opp_history = await self.db.get_user_history(opponent.id, opponent.name)
        
        u_matches, u_wins, u_losses, u_rate = TicketEmbeds.calculate_ranked_stats(requester.id, requester.name, user_history)
        o_matches, o_wins, o_losses, o_rate = TicketEmbeds.calculate_ranked_stats(opponent.id, opponent.name, opp_history)
        
        u_rank = await self.db.get_player_rank(requester.id) or "Unranked"
        o_rank = await self.db.get_player_rank(opponent.id) or "Unranked"
        
        embed, tier_file = TicketEmbeds.create_ranked_1v1_ticket_embed(
            user=requester,
            opponent_name=opponent.name,
            u_rank=u_rank,
            o_rank=o_rank,
            u_rate=u_rate,
            o_rate=o_rate,
            u_matches=u_matches,
            o_matches=o_matches
        )
        embed.add_field(
            name="Out-of-Range Match",
            value="This match was accepted outside the 5-rank window.",
            inline=False
        )

        send_kwargs = {
            "content": f"{requester.mention} {opponent.mention} {observer_mention}",
            "embed": embed
        }
        site_view = TicketEmbeds.ranked_site_view()
        if site_view:
            send_kwargs["view"] = site_view
        if tier_file:
            send_kwargs["file"] = tier_file

        await channel.send(**send_kwargs)
    
    async def create_observation_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        
        try:
            existing_ticket = await self.db.tickets.find_one({"user_id": user.id, "status": "open"})
            if existing_ticket:
                existing_channel = guild.get_channel(existing_ticket["channel_id"])
                if not existing_channel:
                    try:
                        existing_channel = await guild.fetch_channel(existing_ticket["channel_id"])
                    except Exception:
                        existing_channel = None
                if not existing_channel:
                    await self.db.close_ticket(existing_ticket["channel_id"], self.bot.user.id)
                else:
                    await interaction.edit_original_response(content=f"You already have an open ticket in {existing_channel.mention}! Please close it before opening a new one.", view=None)
                    return
                
            ticket_service = self.bot.container.get('TicketService')
            if not ticket_service:
                await interaction.edit_original_response(content="Ticket service unavailable! Please contact an admin.", view=None)
                return
                
            is_valid, error_msg = await ticket_service.validate_observation_request(user.id)
            if not is_valid:
                await interaction.edit_original_response(content=error_msg, view=None)
                return
                
            category = guild.get_channel(Config.TICKET_CATEGORY_ID)
            if not category:
                try:
                    category = await guild.fetch_channel(Config.TICKET_CATEGORY_ID)
                except Exception:
                    category = None
            if not category:
                await interaction.edit_original_response(content="Ticket category not configured or not found!", view=None)
                return
            
            observer_mention = get_observer_mention(guild)
            
            overwrites = get_observer_overwrites(guild, {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }, ticket_type="Personal Observation")
            
            channel_name = f"obs-{user.name}".lower().replace(" ", "-")
            try:
                channel = await asyncio.wait_for(
                    guild.create_text_channel(
                        name=channel_name,
                        category=category,
                        overwrites=overwrites,
                        topic=f"Personal Observation | User: {user.name}"
                    ),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                await interaction.edit_original_response(content="⚠️ Creating channel timed out (Discord rate limit or API delay). Please try again in a minute.", view=None)
                return
            except Exception as e:
                await interaction.edit_original_response(content=f"Failed to create channel: {e}", view=None)
                return
            
            ticket_id = await self.db.create_ticket(channel.id, user.id, "Personal Observation")
            print(f"Ticket {ticket_id} saved")
            
            total_obs = await self.db.get_user_observation_count(user.id)
            u_rank = await self.db.get_player_rank(user.id) or "Unranked"
            user_stats = f"**Rank**: `{u_rank}`\n**Total Observations**: `{total_obs}`"
            
            embed = TicketEmbeds.ticket_created(
                "Personal Observation", user, user_stats=user_stats
            )
            
            await channel.send(
                content=f"{user.mention} {observer_mention}",
                embed=embed
            )
            
            await interaction.edit_original_response(content=f"Ticket created! {channel.mention}", view=None)
        except Exception as e:
            print(f"Unhandled error in create_observation_ticket: {e}")
            try:
                await interaction.edit_original_response(content=f"An unexpected error occurred: {e}", view=None)
            except Exception:
                pass

    @app_commands.command(name="close", description="Close the current ticket")
    async def close(self, interaction: discord.Interaction):
        if not interaction.channel.name.startswith(("ranked-", "obs-")):
            await interaction.response.send_message("This command can only be used in ticket channels!", ephemeral=True)
            return
        
        ticket_data = await self.db.get_ticket_by_channel(interaction.channel.id)
        if not ticket_data:
            await interaction.response.send_message("Ticket not found in database!", ephemeral=True)
            return
        
        status = ticket_data.get("status")
        if status == "processing_failed":
            await interaction.response.send_message(
                "This ticket is locked because an earlier close attempt failed during processing. Please ask an admin to review it.",
                ephemeral=True
            )
            return

        if status in ["processing", "processing_modal"]:
            processing_time = ticket_data.get("processing_since")
            now = datetime.utcnow()
            stuck = False
            if processing_time:
                try:
                    pt = processing_time if isinstance(processing_time, datetime) else datetime.fromisoformat(str(processing_time))
                    if (now - pt).total_seconds() > 60:
                        stuck = True
                except (ValueError, TypeError):
                    stuck = True
            else:
                stuck = True
                
            if stuck:
                if status == "processing":
                    await self.db.mark_ticket_processing_failed(
                        interaction.channel.id,
                        RuntimeError("Ticket processing exceeded the 60-second safety limit")
                    )
                    await interaction.response.send_message(
                        "This close attempt appears stuck and has been locked for admin review to prevent duplicate results.",
                        ephemeral=True
                    )
                    return
                await self.db.tickets.update_one(
                    {"channel_id": interaction.channel.id, "status": "processing_modal"},
                    {"$set": {"status": "open"}, "$unset": {"processing_since": ""}}
                )
                ticket_data["status"] = "open"
            else:
                await interaction.response.send_message("This ticket is currently being processed. Please wait...", ephemeral=True)
                return
            
        if ticket_data.get("status") == "closed":
            await interaction.response.send_message("This ticket was already closed in the database. Deleting channel now...", ephemeral=True)
            await asyncio.sleep(3)
            try:
                await interaction.channel.delete()
            except discord.errors.NotFound:
                pass
            return
        
        is_obs = is_observer_or_trial(interaction.user, ticket_data['ticket_type'])
        is_owner = interaction.user.id == ticket_data['user_id']
        
        if ticket_data['ticket_type'] == "Personal Observation":
            has_no_obs = False
            if hasattr(Config, 'NO_PERSONAL_OBS_ROLE_ID') and Config.NO_PERSONAL_OBS_ROLE_ID:
                no_obs_role = interaction.guild.get_role(Config.NO_PERSONAL_OBS_ROLE_ID)
                if no_obs_role and no_obs_role in interaction.user.roles:
                    has_no_obs = True
            if has_no_obs:
                await interaction.response.send_message("You don't have permission to close Personal Observation tickets!", ephemeral=True)
                return
        
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
                if ticket_data.get('status') != 'open':
                    await interaction.response.send_message(f"This ticket is already closed or being processed. (Current status: {ticket_data.get('status')})", ephemeral=True)
                    return
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
        ticket_data = await self.db.claim_ticket_for_processing(interaction.channel.id)
        if not ticket_data:
            await interaction.followup.send("This ticket has already been closed or is being processed!", ephemeral=True)
            return
        
        try:
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
            await self.db.update_ranked_cooldown(ticket_data['user_id'])
            if not await self.db.finalize_processed_ticket(interaction.channel.id, interaction.user.id):
                raise RuntimeError("Ticket processing state changed before completion")
        except Exception as e:
            await self.db.mark_ticket_processing_failed(interaction.channel.id, e)
            await interaction.followup.send(
                "The match could not be completed safely. The ticket has been locked for admin review to prevent the result from being applied twice.",
                ephemeral=True
            )
            print(f"Error committing ranked close: {e}")
            return

        try:
            
            # Auto-assign tier roles for both players
            from utils.role_manager import update_tier_role
            await update_tier_role(interaction.guild, winner_id, new_win)
            await update_tier_role(interaction.guild, loser_id, new_lose)
            
            ticket_service = self.bot.container.get('TicketService')
            if ticket_service:
                await ticket_service.check_and_notify_rank_change(winner_id, new_win)
                await ticket_service.check_and_notify_rank_change(loser_id, new_lose)
            
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
            # The database commit already succeeded. Never reopen here: doing so
            # would allow the same match result to be applied a second time.
            await interaction.followup.send(
                "The match result was saved, but a Discord follow-up action failed. An admin may need to update roles, logs, or delete this channel manually.",
                ephemeral=True
            )
            print(f"Error after ranked close commit: {e}")

    async def process_ranked_cancel(self, interaction: discord.Interaction, modal: CloseRankedCancelModal):
        result = await self.db.tickets.find_one_and_update(
            {"channel_id": interaction.channel.id, "status": "open"},
            {"$set": {"status": "processing", "processing_since": str(datetime.utcnow())}}
        )
        if not result:
            await interaction.followup.send("This ticket has already been closed or is being processed!", ephemeral=True)
            return
        ticket_data = result
        
        try:
            await self.db.close_ticket(interaction.channel.id, interaction.user.id)

            
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
            {"$set": {"status": "processing", "processing_since": str(datetime.utcnow())}}
        )
        if not result:
            await interaction.followup.send("This ticket has already been closed or is being processed!", ephemeral=True)
            return
        ticket_data = result
        
        try:
            user_id = ticket_data['user_id']
            old_rank = await self.db.get_player_rank(user_id)
            
            from utils.ladder_utils import parse_rank
            parsed = parse_rank(end_rank)
            if parsed:
                tier, target_num = parsed
                current_count = await self.db.get_tier_count(tier)
                
                if target_num > current_count + 1:
                    await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
                    await interaction.followup.send(f"Invalid Rank Gap! You cannot place a player at {end_rank} because there are only {current_count} players in {tier}. The maximum rank you can assign is {tier} {current_count + 1}.", ephemeral=True)
                    return
            
            if end_rank.lower() == "unranked":
                success, old_r = await self.db.unrank_player(user_id, movement_source="observation")
                actual_new_rank = "Unranked"
                if not success and old_r not in ["You are not currently ranked.", "You are already unranked."]:
                    await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
                    await interaction.followup.send(f"Failed to unrank: {old_r}", ephemeral=True)
                    return
            else:
                success, actual_new_rank = await self.db.force_set_player_rank(
                    user_id,
                    end_rank,
                    movement_source="observation",
                )
                
                if not success:
                    await self.db.tickets.update_one({"channel_id": interaction.channel.id}, {"$set": {"status": "open"}})
                    await interaction.followup.send("Failed to update rank. Please ensure the rank is formatted correctly.", ephemeral=True)
                    return
            
            # Auto-assign tier role for observed player
            from utils.role_manager import update_tier_role
            await update_tier_role(interaction.guild, user_id, actual_new_rank)
            
            ticket_service = self.bot.container.get('TicketService')
            if ticket_service:
                await ticket_service.check_and_notify_rank_change(user_id, actual_new_rank)
            
            await self.db.add_observation_result(
                ticket_data['id'],
                interaction.user.id,
                interaction.user.name,
                old_rank if old_rank else "Unranked",
                actual_new_rank,
                modal.note.value if modal.note.value else None
            )
            await self.db.close_ticket(interaction.channel.id, interaction.user.id)
            await self.db.update_obs_cooldown(user_id)
            
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
