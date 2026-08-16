import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
from datetime import datetime

from config import Config
from database import Database
from utils.embeds import TicketEmbeds

from views.ticket_views import TicketView
from utils.ticket_utils import get_observer_mention


class TicketAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.db_ready = True

    @commands.Cog.listener()
    async def on_ready(self):
        print("TicketAdmin cog loaded")

    @app_commands.command(name="cleanghostchannels", description="Delete out-of-range ticket channels that are missing from the DB (Observer/Admin only)")
    async def clean_ghost_channels(self, interaction: discord.Interaction):
        is_admin = interaction.user.guild_permissions.administrator
        has_observer = any(role.id == Config.OBSERVER_ROLE_ID for role in interaction.user.roles)
        if not (is_admin or has_observer):
            await interaction.response.send_message("You must be an Observer or Administrator to use this command.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        category = interaction.guild.get_channel(Config.TICKET_CATEGORY_ID)
        if not category:
            await interaction.followup.send("Ticket category not configured!")
            return
            
        ghost_count = 0
        for channel in category.text_channels:
            if channel.name.startswith("ranked-"):
                ticket = await self.db.tickets.find_one({"channel_id": channel.id})
                if not ticket:
                    try:
                        await channel.delete()
                        ghost_count += 1
                        await asyncio.sleep(0.5)  # Rate limit protection
                    except Exception:
                        pass
                        
        await interaction.followup.send(f"Found and deleted {ghost_count} ghost ticket channels!")
    
    @app_commands.command(name="cleanghosttickets", description="Close orphan DB tickets whose channels no longer exist")
    async def clean_ghost_tickets(self, interaction: discord.Interaction):
        if not interaction.permissions.administrator and interaction.user.id != Config.MASTER_ADMIN_ID:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        cursor = self.db.tickets.find({"status": {"$in": ["open", "pending_accept"]}})
        open_tickets = await cursor.to_list(length=None)
        
        if not open_tickets:
            await interaction.followup.send("No open tickets in the database.", ephemeral=True)
            return
        
        cleaned = 0
        for ticket in open_tickets:
            channel = self.bot.get_channel(ticket['channel_id'])
            if not channel:
                # Double-check with API in case it's just not cached
                try:
                    channel = await self.bot.fetch_channel(ticket['channel_id'])
                except Exception:
                    channel = None
            
            if not channel:
                await self.db.close_ticket(ticket['channel_id'], self.bot.user.id)
                cleaned += 1
        
        await interaction.followup.send(
            f"Cleaned up **{cleaned}** ghost ticket(s) from the database.\n"
            f"({len(open_tickets) - cleaned} tickets still have valid channels.)",
            ephemeral=True
        )
    
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
    
    @app_commands.command(name="refreshtickets", description="Re-edit embeds in all open ticket channels with updated instructions")
    async def refresh_tickets(self, interaction: discord.Interaction):
        if not interaction.permissions.administrator and interaction.user.id != Config.MASTER_ADMIN_ID:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        cursor = self.db.tickets.find({"status": {"$in": ["open", "pending_accept"]}})
        open_tickets = await cursor.to_list(length=None)
        
        if not open_tickets:
            await interaction.followup.send("No open tickets found.", ephemeral=True)
            return
        
        updated = 0
        skipped = 0
        
        for ticket in open_tickets:
            channel = self.bot.get_channel(ticket['channel_id'])
            if not channel:
                skipped += 1
                continue
            
            try:
                async for message in channel.history(limit=20, oldest_first=True):
                    if message.author == self.bot.user and message.embeds:
                        embed = message.embeds[0]
                        is_ticket_embed = False
                        if embed.title and ("Ticket Created" in embed.title or "Lobby" in embed.title or "Promotion Match" in embed.title or "Personal Observation" in embed.title):
                            is_ticket_embed = True
                        if embed.footer and embed.footer.text and "Wait for an observer" in embed.footer.text:
                            is_ticket_embed = True
                            
                        if is_ticket_embed:
                            ticket_type = ticket.get('ticket_type', '')
                            
                            if ticket_type == "Ranked 1v1":
                                user = interaction.guild.get_member(ticket['user_id'])
                                if not user:
                                    skipped += 1
                                    break
                                
                                opponent_name = ticket.get('opponent_name', 'Unknown')
                                opponent_id = ticket.get('opponent_id')
                                
                                user_history = await self.db.get_user_history(user.id, user.name)
                                opp_history = await self.db.get_user_history(opponent_id, opponent_name) if opponent_id else []
                                
                                _, _, _, u_rate = TicketEmbeds.calculate_ranked_stats(user.id, user.name, user_history)
                                _, _, _, o_rate = TicketEmbeds.calculate_ranked_stats(opponent_id, opponent_name, opp_history) if opponent_id else (0, 0, 0, 0.0)
                                
                                u_rank = await self.db.get_player_rank(user.id) or "Unranked"
                                o_rank = await self.db.get_player_rank(opponent_id) or "Unranked" if opponent_id else "Unranked"
                                
                                new_embed, tier_file = TicketEmbeds.create_ranked_1v1_ticket_embed(
                                    user=user,
                                    opponent_name=opponent_name,
                                    u_rank=u_rank,
                                    o_rank=o_rank,
                                    u_rate=u_rate,
                                    o_rate=o_rate
                                )
                                
                                if tier_file:
                                    await message.edit(embed=new_embed, attachments=[tier_file])
                                else:
                                    await message.edit(embed=new_embed)
                                
                                updated += 1
                                break
                            else:
                                # For other ticket types, keep the old manual field refresh logic
                                new_embed = embed.copy()
                                new_fields = []
                                for field in new_embed.fields:
                                    if field.name not in ("Instructions", "How It Works"):
                                        new_fields.append(field)
                                
                                new_embed.clear_fields()
                                for f in new_fields:
                                    new_embed.add_field(name=f.name, value=f.value, inline=f.inline)
                                
                                instructions = (
                                    "- An observer will drop in to watch you play\n"
                                    "- Show them what you got — they're sizing up your skill level\n"
                                    "- After they've seen enough, they'll set or adjust your rank\n"
                                    "- Your rank can go up, down, or stay the same depending on how you perform\n\n"
                                    "Hang tight and wait for an observer before you start."
                                )
                                
                                new_embed.add_field(name="How It Works", value=instructions, inline=False)
                                await message.edit(embed=new_embed)
                                updated += 1
                                break
                else:
                    skipped += 1
            except Exception as e:
                print(f"Error refreshing ticket in {channel.id}: {e}")
                skipped += 1
        
        await interaction.followup.send(f"Done! Updated **{updated}** ticket(s), skipped **{skipped}**.", ephemeral=True)

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


async def setup(bot):
    await bot.add_cog(TicketAdmin(bot))
