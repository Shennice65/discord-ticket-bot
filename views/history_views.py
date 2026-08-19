import discord
import asyncio

from config import Config
from database import Database
from utils.embeds import TicketEmbeds
from utils.clips_utils import is_valid_clip_url, get_clip_source, convert_clip_via_service, validate_and_scrape_medal, check_clip_progress, format_clip_display

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
        if count >= 10:
            await interaction.edit_original_response(content="You've reached the maximum of **10 clips**. Delete one first!")
            return
        
        # Validate URL is Medal or TikTok
        if not is_valid_clip_url(url):
            await interaction.edit_original_response(
                content="Invalid link! Please submit a **Medal.tv** or **TikTok** URL."
            )
            return
        
        source = get_clip_source(url)
        
        # Send to conversion service
        await interaction.edit_original_response(
            content=f"Processing your {source.title()} clip... This may take a moment."
        )
        
        scraped_title = ""
        if source == "medal":
            medal_data = await validate_and_scrape_medal(url)
            if medal_data["valid"] and medal_data["title"]:
                scraped_title = medal_data["title"]
                
        config_doc = await db.db.config.find_one({"_id": "api_keys"})
        clips_service_url = config_doc.get("CLIPS_SERVICE_URL") if config_doc else Config.CLIPS_SERVICE_URL
        
        result = await convert_clip_via_service(url, clips_service_url, title=scraped_title)
        if not result["success"]:
            await interaction.edit_original_response(
                content=f"Failed to start processing: {result['error']}"
            )
            return
            
        task_id = result["task_id"]
        final_result = None
        
        while True:
            await asyncio.sleep(2.5)  # Sleep to avoid ratelimits
            prog_res = await check_clip_progress(task_id, clips_service_url)
            
            if not prog_res["success"]:
                continue
                
            pdata = prog_res["progress_data"]
            status = pdata.get("status", "unknown")
            percent = pdata.get("percent", 0.0)
            detail = pdata.get("detail", "Processing...")
            
            if status == "error":
                await interaction.edit_original_response(
                    content=f"Failed to process clip: {pdata.get('error', 'Unknown error')}"
                )
                return
                
            if status == "completed":
                final_result = pdata.get("result")
                break
                
            # Render progress bar
            bar_len = 10
            filled = int(bar_len * (percent / 100))
            bar = "█" * filled + "░" * (bar_len - filled)
            
            msg = f"**{detail}**\n`[{bar}] {percent}%`"
            try:
                await interaction.edit_original_response(content=msg)
            except:
                pass  # Ignore rare Discord rate limits during rapid updates
        
        if not final_result or not final_result.get("success"):
            await interaction.edit_original_response(content=" Backend finished but returned no data.")
            return

        # Use the final result
        result = final_result
        
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
            content = format_clip_display(clip, len(clips) - 1, len(clips))
            await interaction.edit_original_response(content=content, embed=None, view=self.clips_view_ref)

class ClipNavigationSelect(discord.ui.Select):
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
            placeholder="Select a clip to view...",
            options=options,
            min_values=1,
            max_values=1,
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        self.clips_view_ref.current_page = index
        self.clips_view_ref.update_buttons()
        
        clips = self.clips_view_ref.clips
        content = format_clip_display(clips[index], index, len(clips))
        
        await interaction.response.edit_message(content=content, embed=None, view=self.clips_view_ref)


class ConfirmDeleteClipView(discord.ui.View):
    def __init__(self, clips_view_ref, index: int):
        super().__init__(timeout=60)
        self.clips_view_ref = clips_view_ref
        self.index = index

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        db = interaction.client.db
        target_user = self.clips_view_ref.target_user
        
        if 0 <= self.index < len(self.clips_view_ref.clips):
            clip_to_delete = self.clips_view_ref.clips[self.index]
            clip_page_url = clip_to_delete.get("clip_page_url")
            
            if clip_page_url:
                from utils.clips_utils import delete_clip_from_service
                from config import Config
                config_doc = await db.db.config.find_one({"_id": "api_keys"})
                clips_service_url = config_doc.get("CLIPS_SERVICE_URL") if config_doc else Config.CLIPS_SERVICE_URL
                clips_admin_password = config_doc.get("CLIPS_ADMIN_PASSWORD") if config_doc else Config.CLIPS_ADMIN_PASSWORD
                await delete_clip_from_service(clip_page_url, clips_service_url, clips_admin_password)

            success = await db.delete_user_clip(target_user.id, self.index)
            
            if not success:
                await interaction.edit_original_response(content="Failed to delete clip. It may have already been removed.", view=None)
                return
                
            # Refresh
            self.clips_view_ref.clips = await db.get_user_clips(target_user.id)
            
            if not self.clips_view_ref.clips:
                self.clips_view_ref.current_page = 0
                from utils.embeds import TicketEmbeds
                embed = TicketEmbeds.clips_empty_embed(target_user)
                if self.clips_view_ref.message:
                    await self.clips_view_ref.message.edit(content=None, embed=embed, view=self.clips_view_ref)
            else:
                self.clips_view_ref.current_page = min(self.clips_view_ref.current_page, len(self.clips_view_ref.clips) - 1)
                self.clips_view_ref.update_buttons()
                content = format_clip_display(self.clips_view_ref.clips[self.clips_view_ref.current_page], self.clips_view_ref.current_page, len(self.clips_view_ref.clips))
                if self.clips_view_ref.message:
                    await self.clips_view_ref.message.edit(content=content, embed=None, view=self.clips_view_ref)
                
            await interaction.edit_original_response(content="Clip successfully deleted.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Deletion cancelled.", view=None)


class ClipsPaginationView(discord.ui.View):
    def __init__(self, target_user: discord.Member, clips: list, is_owner: bool):
        super().__init__(timeout=180)
        self.target_user = target_user
        self.clips = clips
        self.is_owner = is_owner
        self.current_page = 0
        self.message = None  # We need a reference to the message to edit it from the modal
        
        # Remove owner-only buttons if not the owner
        if not self.is_owner:
            self.remove_item(self.btn_submit)
            self.remove_item(self.btn_delete_current)
        
        self.update_buttons()
    
    def update_buttons(self):
        """Update select menu options based on current clips."""
        # Remove existing select menu
        for item in self.children[:]:
            if isinstance(item, ClipNavigationSelect):
                self.remove_item(item)
        
        # Add new select menu if there are clips
        if self.clips:
            select = ClipNavigationSelect(self.clips, self.target_user, self)
            self.add_item(select)
            
            if 0 <= self.current_page < len(self.clips):
                clip = self.clips[self.current_page]
                stars = len(clip.get("stars", []))
                skulls = len(clip.get("skulls", []))
                for child in self.children:
                    if getattr(child, "custom_id", "") == "hist_clip_star":
                        child.label = f"⭐ {stars}"
                    elif getattr(child, "custom_id", "") == "hist_clip_skull":
                        child.label = f"💀 {skulls}"
    
    @discord.ui.button(label="⭐ 0", style=discord.ButtonStyle.secondary, row=1, custom_id="hist_clip_star")
    async def btn_star(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_reaction(interaction, "star", button)
        
    @discord.ui.button(label="💀 0", style=discord.ButtonStyle.secondary, row=1, custom_id="hist_clip_skull")
    async def btn_skull(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_reaction(interaction, "skull", button)
        
    @discord.ui.button(label="Submit Clip", style=discord.ButtonStyle.success, row=2)
    async def btn_submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SubmitClipModal(self.target_user, self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Delete Clip", style=discord.ButtonStyle.danger, row=2)
    async def btn_delete_current(self, interaction: discord.Interaction, button: discord.ui.Button):
        if 0 <= self.current_page < len(self.clips):
            title = self.clips[self.current_page].get("title", "Untitled Clip")
            view = ConfirmDeleteClipView(self, self.current_page)
            await interaction.response.send_message(f"Are you sure you want to delete **{title}**?", view=view, ephemeral=True)
        
    async def handle_reaction(self, interaction: discord.Interaction, reaction_type: str, button: discord.ui.Button):
        db = interaction.client.db
        res = await db.toggle_clip_reaction(self.target_user.id, self.current_page, interaction.user.id, reaction_type)
        if not res:
            await interaction.response.send_message("Clip not found.", ephemeral=True)
            return
            
        self.clips = await db.get_user_clips(self.target_user.id)
        if reaction_type == "star":
            button.label = f"⭐ {res['stars']}"
        elif reaction_type == "skull":
            button.label = f"💀 {res['skulls']}"
            
        await interaction.response.edit_message(view=self)


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

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary, custom_id="hist_overview", row=0)
    async def btn_overview(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = TicketEmbeds.history_overview_embed(self.target_user, self.history, self.unrank_info, self.obs_cooldown_days, self.ranked_cooldown_days, current_rank=self.current_rank)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="Ranked Matches", style=discord.ButtonStyle.secondary, custom_id="hist_ranked", row=0)
    async def btn_ranked(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = TicketEmbeds.history_ranked_embed(self.target_user, self.history)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="Observations", style=discord.ButtonStyle.secondary, custom_id="hist_obs", row=0)
    async def btn_obs(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = TicketEmbeds.history_observation_embed(self.target_user, self.history)
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
            msg = await interaction.followup.send(content=None, embed=embed, view=clips_view, ephemeral=True, wait=True)
            clips_view.message = msg
        else:
            clips_view = ClipsPaginationView(self.target_user, clips, is_owner)
            content = format_clip_display(clips[0], 0, len(clips))
            msg = await interaction.followup.send(content=content, embed=None, view=clips_view, ephemeral=True, wait=True)
            clips_view.message = msg

    @discord.ui.button(label="Clear History", style=discord.ButtonStyle.danger, custom_id="hist_clear", row=1)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_admin:
            await interaction.response.send_message("Only administrators can clear history.", ephemeral=True)
            return
            
        view = ClearHistoryView(self.target_user.id, self.target_user.name)
        await interaction.response.send_message("Select history to clear:", view=view, ephemeral=True)


class ShareClipView(discord.ui.View):
    def __init__(self, owner_id: int = 0, clip_index: int = 0, stars: int = 0, skulls: int = 0):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.clip_index = clip_index
        # Set initial label counts (used when first sending the message)
        self.btn_star.label = f"⭐ {stars}"
        self.btn_skull.label = f"💀 {skulls}"

    @discord.ui.button(label="⭐ 0", style=discord.ButtonStyle.secondary, custom_id="shareclip_star")
    async def btn_star(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_reaction(interaction, "star")

    @discord.ui.button(label="💀 0", style=discord.ButtonStyle.secondary, custom_id="shareclip_skull")
    async def btn_skull(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_reaction(interaction, "skull")

    async def handle_reaction(self, interaction: discord.Interaction, reaction_type: str):
        owner_id = self.owner_id
        clip_index = self.clip_index

        if owner_id == 0:
            # After a bot restart, recover owner/clip by looking up the URL in the DB
            msg = interaction.message
            if msg and msg.content:
                import re
                # Extract the clip URL from the message (first URL found)
                url_match = re.search(r"(https?://\S+)", msg.content)
                if url_match:
                    clip_url = url_match.group(1)
                    db = interaction.client.db
                    # Search all players' clips for this URL
                    async for player in db.db.player_clips.find({"clips.clip_page_url": clip_url}):
                        owner_id = player["user_id"]
                        for i, clip in enumerate(player.get("clips", [])):
                            if clip.get("clip_page_url") == clip_url:
                                clip_index = i
                                break
                        break

        if owner_id == 0:
            await interaction.response.send_message("Could not determine clip owner.", ephemeral=True)
            return

        db = interaction.client.db
        res = await db.toggle_clip_reaction(owner_id, clip_index, interaction.user.id, reaction_type)
        if not res:
            await interaction.response.send_message("Clip not found.", ephemeral=True)
            return

        self.btn_star.label = f"⭐ {res['stars']}"
        self.btn_skull.label = f"💀 {res['skulls']}"
        await interaction.response.edit_message(view=self)
