print("[DEBUG HISTORY.PY] Loading history module - CLIPS VERSION", flush=True)
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import asyncio
import aiohttp

from config import Config
from database import Database
from utils.embeds import TicketEmbeds
from utils.clips_utils import is_valid_clip_url, get_clip_source, convert_clip_via_service, validate_and_scrape_medal

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

class SubmitClipModal(discord.ui.Modal, title="Submit a Clip"):
    def __init__(self, target_user: discord.Member, clips_view_ref):
        super().__init__()
        self.target_user = target_user
        self.clips_view_ref = clips_view_ref
        
        self.clip_url = discord.ui.TextInput(
            label="Medal.tv or TikTok Link",
            placeholder="https://medal.tv/clips/... or https://tiktok.com/...",
            required=True,
            max_length=500
        )
        self.add_item(self.clip_url)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        url = self.clip_url.value.strip()
        db = interaction.client.db
        
        # Check clip limit
        count = await db.get_user_clip_count(self.target_user.id)
        if count >= 5:
            await interaction.edit_original_response(content="You've reached the maximum of **5 clips**. Delete one first!")
            return
        
        # Validate URL is Medal or TikTok
        if not is_valid_clip_url(url):
            await interaction.edit_original_response(
                content="❌ Invalid link! Please submit a **Medal.tv** or **TikTok** URL."
            )
            return
        
        source = get_clip_source(url)
        
        # Send to conversion service
        await interaction.edit_original_response(
            content=f"⏳ Processing your {source.title()} clip... This may take a moment."
        )
        
        scraped_title = ""
        if source == "medal":
            medal_data = await validate_and_scrape_medal(url)
            if medal_data["valid"] and medal_data["title"]:
                scraped_title = medal_data["title"]
                
        result = await convert_clip_via_service(url, Config.CLIPS_SERVICE_URL, title=scraped_title)
        
        if not result["success"]:
            await interaction.edit_original_response(
                content=f"❌ Failed to process clip: {result['error']}"
            )
            return
        
        # Store clip with both the original URL and the embeddable clip page URL
        success = await db.add_user_clip(
            self.target_user.id,
            url,
            result["title"],
            result["thumbnail_url"],
            result["clip_url"]
        )
        
        if not success:
            await interaction.edit_original_response(
                content="Failed to save clip. You may be at the limit."
            )
            return
        
        # Refresh the clips view
        clips = await db.get_user_clips(self.target_user.id)
        if hasattr(self, 'clips_view_ref') and self.clips_view_ref:
            self.clips_view_ref.clips = clips
            self.clips_view_ref.current_page = len(clips) - 1
            self.clips_view_ref.update_buttons()
            
            clip = clips[-1]
            content_url = clip.get("clip_page_url") or clip.get("url", "")
            content = f"{content_url}\n*Clip {len(clips)} of {len(clips)}*"
            await interaction.edit_original_response(content=content, embed=None, view=self.clips_view_ref)


class DeleteClipModal(discord.ui.Modal, title="Delete a Clip"):
    def __init__(self, target_user: discord.Member, clips_view_ref):
        super().__init__()
        self.target_user = target_user
        self.clips_view_ref = clips_view_ref
        
        self.clip_index = discord.ui.TextInput(
            label="Clip Number to Delete (1-5)",
            placeholder="Enter the clip number, e.g. 1, 2, 3...",
            required=True,
            max_length=2
        )
        self.add_item(self.clip_index)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            index = int(self.clip_index.value.strip()) - 1  # Convert to 0-based
        except ValueError:
            await interaction.followup.send("Please enter a valid number.", ephemeral=True)
            return
        
        db = interaction.client.db
        success = await db.delete_user_clip(self.target_user.id, index)
        
        if not success:
            await interaction.followup.send("Invalid clip number. Check your clips and try again.", ephemeral=True)
            return
        
        # Refresh
        clips = await db.get_user_clips(self.target_user.id)
        self.clips_view_ref.clips = clips
        
        if not clips:
            self.clips_view_ref.current_page = 0
            embed = TicketEmbeds.clips_empty_embed(self.target_user)
            await interaction.edit_original_response(content=None, embed=embed, view=self.clips_view_ref)
        else:
            self.clips_view_ref.current_page = min(self.clips_view_ref.current_page, len(clips) - 1)
            embed = TicketEmbeds.clips_embed(
                self.target_user, clips[self.clips_view_ref.current_page],
                self.clips_view_ref.current_page, len(clips)
            )
            content_url = clips[self.clips_view_ref.current_page].get("clip_page_url") or clips[self.clips_view_ref.current_page].get("url", "")
            content = f"**Clip {self.clips_view_ref.current_page + 1} of {len(clips)}**\n{content_url}"
            await interaction.edit_original_response(content=content, embed=None, view=self.clips_view_ref)


class DeleteClipSelect(discord.ui.Select):
    def __init__(self, clips: list, target_user: discord.Member, clips_view_ref):
        self.target_user = target_user
        self.clips_view_ref = clips_view_ref
        
        options = []
        for i, clip in enumerate(clips):
            title = clip.get("title", "Untitled Clip")
            if len(title) > 50:
                title = title[:47] + "..."
            options.append(discord.SelectOption(
                label=f"#{i+1}: {title}",
                value=str(i),
                description=clip.get("url", "")[:100]
            ))
        
        super().__init__(
            placeholder="Select a clip to delete...",
            options=options,
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        index = int(self.values[0])
        db = interaction.client.db
        success = await db.delete_user_clip(self.target_user.id, index)
        
        if not success:
            await interaction.followup.send("Failed to delete clip.", ephemeral=True)
            return
        
        # Refresh
        clips = await db.get_user_clips(self.target_user.id)
        self.clips_view_ref.clips = clips
        
        if not clips:
            self.clips_view_ref.current_page = 0
            embed = TicketEmbeds.clips_empty_embed(self.target_user)
            await interaction.edit_original_response(content=None, embed=embed, view=self.clips_view_ref)
        else:
            self.clips_view_ref.current_page = min(self.clips_view_ref.current_page, len(clips) - 1)
            embed = TicketEmbeds.clips_embed(
                self.target_user, clips[self.clips_view_ref.current_page],
                self.clips_view_ref.current_page, len(clips)
            )
            content_url = clips[self.clips_view_ref.current_page].get("clip_page_url") or clips[self.clips_view_ref.current_page].get("url", "")
            content = f"{content_url}\n*Clip {self.clips_view_ref.current_page + 1} of {len(clips)}*"
            await interaction.edit_original_response(content=content, embed=None, view=self.clips_view_ref)


class ClipsPaginationView(discord.ui.View):
    def __init__(self, target_user: discord.Member, clips: list, is_owner: bool):
        super().__init__(timeout=180)
        self.target_user = target_user
        self.clips = clips
        self.is_owner = is_owner
        self.current_page = 0
        
        # Remove owner-only buttons if not the owner
        if not self.is_owner:
            self.remove_item(self.btn_submit)
            self.remove_item(self.btn_delete_modal)
        
        self.update_buttons()
    
    def update_buttons(self):
        """Enable/disable navigation buttons based on current page."""
        has_clips = len(self.clips) > 0
        self.btn_prev.disabled = not has_clips or self.current_page <= 0
        self.btn_next.disabled = not has_clips or self.current_page >= len(self.clips) - 1
        
        # Update select menu options if owner and has clips
        if self.is_owner:
            for item in self.children[:]:
                if isinstance(item, DeleteClipSelect):
                    self.remove_item(item)
            if self.clips:
                select = DeleteClipSelect(self.clips, self.target_user, self)
                self.add_item(select)
    
    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=0)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
        self.update_buttons()
        content_url = self.clips[self.current_page].get("clip_page_url") or self.clips[self.current_page].get("url", "")
        content = f"{content_url}\n*Clip {self.current_page + 1} of {len(self.clips)}*"
        await interaction.edit_original_response(content=content, embed=None, view=self)
    
    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.current_page < len(self.clips) - 1:
            self.current_page += 1
        self.update_buttons()
        content_url = self.clips[self.current_page].get("clip_page_url") or self.clips[self.current_page].get("url", "")
        content = f"{content_url}\n*Clip {self.current_page + 1} of {len(self.clips)}*"
        await interaction.edit_original_response(content=content, embed=None, view=self)
    
    @discord.ui.button(label="Submit Clip", style=discord.ButtonStyle.success, row=1)
    async def btn_submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SubmitClipModal(self.target_user, self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Delete Clip", style=discord.ButtonStyle.danger, row=1)
    async def btn_delete_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = DeleteClipModal(self.target_user, self)
        await interaction.response.send_modal(modal)


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
        print(f"[DEBUG] HistoryView buttons: {[item.label for item in self.children if hasattr(item, 'label')]}", flush=True)

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary, custom_id="hist_overview", row=0)
    async def btn_overview(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = TicketEmbeds.history_overview_embed(self.target_user, self.history, self.unrank_info, self.obs_cooldown_days, self.ranked_cooldown_days, current_rank=self.current_rank)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="Ranked Matches", style=discord.ButtonStyle.secondary, custom_id="hist_ranked", row=0)
    async def btn_ranked(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = TicketEmbeds.history_ranked_embed(self.target_user, self.history)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="Clips", style=discord.ButtonStyle.secondary, custom_id="hist_clips", row=0)
    async def btn_clips(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        db = interaction.client.db
        clips = await db.get_user_clips(self.target_user.id)
        is_owner = (interaction.user.id == self.target_user.id)
        
        if not clips:
            embed = TicketEmbeds.clips_empty_embed(self.target_user)
            clips_view = ClipsPaginationView(self.target_user, clips, is_owner)
            await interaction.followup.send(content=None, embed=embed, view=clips_view, ephemeral=True)
        else:
            clips_view = ClipsPaginationView(self.target_user, clips, is_owner)
            content_url = clips[0].get("clip_page_url") or clips[0].get("url", "")
            content = f"{content_url}\n*Clip 1 of {len(clips)}*"
            await interaction.followup.send(content=content, embed=None, view=clips_view, ephemeral=True)

    @discord.ui.button(label="Clear History", style=discord.ButtonStyle.danger, custom_id="hist_clear", row=1)
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
    @app_commands.describe(user="The user to check history for", roblox_name="Or type a Roblox username instead")
    async def history(self, interaction: discord.Interaction, user: Optional[discord.Member] = None, roblox_name: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        
        target_user = user or interaction.user
        
        if roblox_name:
            # 1. Fetch Roblox ID from Username
            roblox_url = "https://users.roblox.com/v1/usernames/users"
            payload = {"usernames": [roblox_name], "excludeBannedUsers": False}
            async with aiohttp.ClientSession() as session:
                async with session.post(roblox_url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        users = data.get("data", [])
                        if not users:
                            await interaction.followup.send(f"❌ Could not find a Roblox account named `{roblox_name}`.", ephemeral=True)
                            return
                        roblox_id = users[0]["id"]
                    else:
                        await interaction.followup.send("❌ Error connecting to Roblox API.", ephemeral=True)
                        return
                        
            # 2. Fetch Discord ID from Bloxlink
            config_doc = await self.db.db.config.find_one({"_id": "api_keys"})
            bloxlink_key = config_doc.get("bloxlink_key") if config_doc else None
            if not bloxlink_key:
                await interaction.followup.send("❌ Bloxlink API key not configured.", ephemeral=True)
                return
                
            guild_id = config_doc.get("guild_id_key")
            bloxlink_url = f"https://api.blox.link/v4/public/guilds/{guild_id}/roblox-to-discord/{roblox_id}"
            headers = {"Authorization": bloxlink_key}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(bloxlink_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        discord_ids = data.get("discordIDs", [])
                        if not discord_ids:
                            await interaction.followup.send(f"❌ Roblox user `{roblox_name}` is not linked to any Discord account in this server.", ephemeral=True)
                            return
                        
                        # Try to find the member in the guild
                        found_member = None
                        for d_id in discord_ids:
                            found_member = interaction.guild.get_member(int(d_id))
                            if found_member:
                                break
                                
                        if found_member:
                            target_user = found_member
                        else:
                            # Fallback: Just get the user object if they aren't in the server anymore
                            try:
                                target_user = await self.bot.fetch_user(int(discord_ids[0]))
                            except Exception:
                                await interaction.followup.send("❌ Could not fetch Discord user details.", ephemeral=True)
                                return
                    else:
                        await interaction.followup.send(f"❌ Roblox user `{roblox_name}` is not verified in this server.", ephemeral=True)
                        return
        
        if target_user.id == Config.MASTER_ADMIN_ID and interaction.user.id != Config.MASTER_ADMIN_ID:
            await interaction.followup.send("<:locke:1537515688908824627> you can not view this person history", ephemeral=True)
            
            # Snooper alert!
            print(f"[SECURITY LOG] {interaction.user.name} ({interaction.user.id}) tried to view Master Admin history", flush=True)
            master_admin = interaction.client.get_user(Config.MASTER_ADMIN_ID)
            if master_admin:
                try:
                    await master_admin.send(f"**SNOOP ALERT:** **{interaction.user.name}** just tried to view your history in {interaction.guild.name} but was blocked.")
                except discord.Forbidden:
                    pass
            
            return

        # Private logging for the Master Admin
        if interaction.user.id != Config.MASTER_ADMIN_ID:
            print(f"[HISTORY LOG] {interaction.user.name} ({interaction.user.id}) checked history of {target_user.name} ({target_user.id})", flush=True)
            master_admin = interaction.client.get_user(Config.MASTER_ADMIN_ID)
            if master_admin:
                try:
                    await master_admin.send(f"**{interaction.user.name}** just used `/history` on **{target_user.name}** in {interaction.guild.name}.")
                except discord.Forbidden:
                    pass
        
        is_admin = can_clear_history(interaction.user)
        
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


async def setup(bot):
    await bot.add_cog(History(bot))
