print("[DEBUG HISTORY.PY] Loading history module - CLIPS VERSION", flush=True)
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from config import Config
from utils.embeds import TicketEmbeds
from utils.clips_utils import convert_clip_via_service, check_clip_progress

# Import our modularized views
from views.history_views import (
    HistoryView, 
    ConfirmClearModal,
    ShareClipView
)

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


class History(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
    
    @app_commands.command(name="stats", description="View a user's ranked and observation history")
    @app_commands.describe(user="The user to check history for")
    async def stats(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        await interaction.response.defer(ephemeral=True)
        
        target_user = user or interaction.user
        
        if target_user.id == Config.MASTER_ADMIN_ID and interaction.user.id != Config.MASTER_ADMIN_ID:
            await interaction.followup.send("you can not view this person history", ephemeral=True)
            
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
                    await master_admin.send(f"**{interaction.user.name}** just used `/stats` on **{target_user.name}** in {interaction.guild.name}.")
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

    @app_commands.command(name="uploadclip", description="Upload a video clip directly from your device")
    @app_commands.describe(video="The video file to upload (max 25MB)")
    async def uploadclip(self, interaction: discord.Interaction, video: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        
        # Check if content type is video
        if not video.content_type or not video.content_type.startswith("video/"):
            await interaction.followup.send("Please upload a valid video file.")
            return

        db = interaction.client.db
        
        # Check clip limit
        count = await db.get_user_clip_count(interaction.user.id)
        if count >= 10:
            await interaction.followup.send("You've reached the maximum of **10 clips**. Delete one first via `/stats`!")
            return
            
        await interaction.followup.send("Processing your uploaded clip... This may take a moment.")
        
        # Get config
        config_doc = await db.db.config.find_one({"_id": "api_keys"})
        from config import Config
        clips_service_url = config_doc.get("CLIPS_SERVICE_URL") if config_doc else Config.CLIPS_SERVICE_URL
        
        # Send to conversion service using the Discord attachment URL
        import asyncio
        
        result = await convert_clip_via_service(video.url, clips_service_url, title=video.filename)
        
        if not result["success"]:
            await interaction.edit_original_response(content=f"Failed to start processing: {result['error']}")
            return
            
        task_id = result["task_id"]
        final_result = None
        
        while True:
            await asyncio.sleep(2.5)
            prog_res = await check_clip_progress(task_id, clips_service_url)
            
            if not prog_res["success"]:
                continue
                
            pdata = prog_res["progress_data"]
            status = pdata.get("status", "unknown")
            percent = pdata.get("percent", 0.0)
            detail = pdata.get("detail", "Processing...")
            
            if status == "error":
                await interaction.edit_original_response(content=f"Failed to process clip: {pdata.get('error', 'Unknown error')}")
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
                pass
                
        if not final_result or not final_result.get("success"):
            await interaction.edit_original_response(content="Backend finished but returned no data.")
            return

        result = final_result
        
        # Store clip
        success = await db.add_user_clip(
            interaction.user.id,
            video.url,
            result.get("title", video.filename),
            result.get("thumbnail_url", ""),
            result.get("clip_url", "")
        )
        
        if not success:
            await interaction.edit_original_response(content="Failed to save clip. You may be at the limit.")
            return
            
        await interaction.edit_original_response(content=f"Successfully uploaded and saved **{video.filename}**! View it in your `/stats`.")

    @app_commands.command(name="shareclip", description="Share a clip to the channel")
    @app_commands.describe(user="The user whose clip to share (defaults to yourself)")
    async def shareclip(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target_user = user or interaction.user
        db = interaction.client.db
        clips = await db.get_user_clips(target_user.id)
        
        if not clips:
            if target_user.id == interaction.user.id:
                await interaction.response.send_message("You don't have any clips to share! Upload some using `/uploadclip` or add them via `/stats`.", ephemeral=True)
            else:
                await interaction.response.send_message(f"**{target_user.display_name}** doesn't have any clips.", ephemeral=True)
            return
        
        # Build select menu options from clips
        options = []
        for i, clip in enumerate(clips[:25]):  # Discord select max 25 options
            title = clip.get("title", "Untitled Clip")
            if len(title) > 80:
                title = title[:77] + "..."
            options.append(discord.SelectOption(
                label=f"#{i+1}: {title}",
                value=str(i)
            ))
        
        view = ShareClipSelectView(target_user, clips, options)
        await interaction.response.send_message(
            f"Select a clip from **{target_user.display_name}** to share:",
            view=view,
            ephemeral=True
        )


class ShareClipSelectView(discord.ui.View):
    """Ephemeral dropdown for picking which clip to share publicly."""
    def __init__(self, target_user, clips, options):
        super().__init__(timeout=60)
        self.target_user = target_user
        self.clips = clips
        
        self.select = discord.ui.Select(
            placeholder="Choose a clip to share...",
            options=options
        )
        self.select.callback = self.on_select
        self.add_item(self.select)
    
    async def on_select(self, interaction: discord.Interaction):
        index = int(self.select.values[0])
        clip_data = self.clips[index]
        content_url = clip_data.get("clip_page_url") or clip_data.get("url", "")
        
        # Build uploaded date string
        date_str = ""
        submitted_at = clip_data.get("submitted_at")
        if submitted_at:
            try:
                from datetime import datetime
                if submitted_at.endswith('Z'):
                    submitted_at = submitted_at[:-1] + '+00:00'
                dt = datetime.fromisoformat(submitted_at)
                timestamp = int(dt.timestamp())
                date_str = f" • Uploaded <t:{timestamp}:d>"
            except Exception:
                pass
        
        stars = len(clip_data.get("stars", []))
        skulls = len(clip_data.get("skulls", []))
        
        view = ShareClipView(self.target_user.id, index, stars, skulls)
        
        # Send public message to the channel
        await interaction.channel.send(
            f"**{self.target_user.display_name}'s clip**{date_str} • Shared by {interaction.user.mention}\n{content_url}",
            view=view
        )
        
        # Dismiss the ephemeral picker
        await interaction.response.edit_message(content="Clip shared!", view=None)


async def setup(bot):
    await bot.add_cog(History(bot))
