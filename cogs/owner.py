import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import io
from config import Config

class SysInfoView(discord.ui.View):
    def __init__(self, cog, interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.cog = cog
        self.original_user_id = interaction.user.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("You cannot use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        embed = await self.cog.build_sysinfo_embed()
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Clear Context Cache", style=discord.ButtonStyle.danger)
    async def clear_cache_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        chat_cog = self.cog.bot.get_cog("Chat")
        if chat_cog and hasattr(chat_cog, "context_builder"):
            chat_cog.context_builder.tracker.recent_messages.clear()
            await interaction.followup.send("Context cache cleared!", ephemeral=True)
        embed = await self.cog.build_sysinfo_embed()
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Brain X-Ray", style=discord.ButtonStyle.secondary)
    async def xray_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        chat_cog = self.cog.bot.get_cog("Chat")
        if not chat_cog or not hasattr(chat_cog, "context_builder"):
            await interaction.followup.send("Chat system unavailable.", ephemeral=True)
            return
            
        dump = []
        for channel_id, messages in chat_cog.context_builder.tracker.recent_messages.items():
            dump.append(f"--- Channel {channel_id[1]} (Guild {channel_id[0]}) ---")
            for msg_id, msg in messages.items():
                content_preview = msg.content.replace('\n', ' ')
                dump.append(f"[{msg.author_name}]: {content_preview}")
            dump.append("")
            
        if not dump:
            text = "Live cache is completely empty."
        else:
            text = "\n".join(dump)
            
        file = discord.File(io.BytesIO(text.encode("utf-8")), filename="brain_xray.txt")
        await interaction.followup.send("Here is the exact live cache currently retained in RAM:", file=file, ephemeral=True)

class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def is_owner(user_id: int) -> bool:
        return user_id in [Config.MASTER_ADMIN_ID, Config.SHEN_ID]

    @app_commands.command(name="block", description="Block a user from using bot commands")
    @app_commands.describe(user="The user to block")
    async def block(self, interaction: discord.Interaction, user: discord.User):
        if not self.is_owner(interaction.user.id):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        blocked_user_ids = await self.bot.db.get_setting("blocked_user_ids", [])
        if user.id in blocked_user_ids:
            await interaction.response.send_message(f"{user.mention} is already blocked.", ephemeral=True)
            return

        blocked_user_ids.append(user.id)
        await self.bot.db.set_setting("blocked_user_ids", blocked_user_ids)
        await interaction.response.send_message(f"Blocked {user.mention} from using bot commands.", ephemeral=True)

    @app_commands.command(name="unblock", description="Allow a blocked user to use bot commands again")
    @app_commands.describe(user="The user to unblock")
    async def unblock(self, interaction: discord.Interaction, user: discord.User):
        if not self.is_owner(interaction.user.id):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        blocked_user_ids = await self.bot.db.get_setting("blocked_user_ids", [])
        if user.id not in blocked_user_ids:
            await interaction.response.send_message(f"{user.mention} is not blocked.", ephemeral=True)
            return

        blocked_user_ids.remove(user.id)
        await self.bot.db.set_setting("blocked_user_ids", blocked_user_ids)
        await interaction.response.send_message(f"Unblocked {user.mention}.", ephemeral=True)

    @app_commands.command(name="chat", description="Send a message in the current channel (Owner and Co-Owner only)")
    @app_commands.describe(message="The message to send")
    async def chat(self, interaction: discord.Interaction, message: str):
        is_master = interaction.user.id in [Config.MASTER_ADMIN_ID, Config.SHEN_ID]
        
        co_owner_role = None
        if interaction.guild and Config.CO_OWNER_ROLE_ID:
            co_owner_role = interaction.guild.get_role(Config.CO_OWNER_ROLE_ID)
        is_co_owner = bool(
            co_owner_role
            and co_owner_role in getattr(interaction.user, "roles", ())
        )
        
        if not (is_master or is_co_owner):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message(
                "Messages cannot be sent in this channel.", ephemeral=True
            )
            return

        try:
            await channel.send(message)
        except discord.Forbidden:
            await interaction.response.send_message(
                "I do not have permission to send messages in this channel.", ephemeral=True
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord could not send the message. Please try again.", ephemeral=True
            )
            return

        await interaction.response.send_message("Message sent.", ephemeral=True)

    async def build_sysinfo_embed(self) -> discord.Embed:
        # DB setup
        db = getattr(self.bot, "db", None)
        collection = getattr(getattr(db, "db", None), "ai_audit", None)
        memory_collection = getattr(getattr(db, "db", None), "chat_memory", None)
        
        # 1. Active Configuration
        active_provider = getattr(Config, "AI_PROVIDER", "deepseek").title()
        deepseek_model = getattr(Config, "DEEPSEEK_MODEL", "deepseek-flash")
        gemini_model = getattr(Config, "GEMINI_MODEL", "gemini-1.5-flash")
        
        # 2. 24-Hour API Usage
        tokens_used = 0
        total_requests = 0
        if collection is not None:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            pipeline = [
                {"$match": {"created_at": {"$gte": yesterday}}},
                {"$group": {
                    "_id": None, 
                    "total_tokens": {"$sum": "$usage.total_tokens"},
                    "count": {"$sum": 1}
                }}
            ]
            # Since motor driver uses async for
            async for doc in collection.aggregate(pipeline):
                tokens_used = doc.get("total_tokens", 0)
                total_requests = doc.get("count", 0)
                
        # 3. Live State / Context Bloat
        chat_cog = self.bot.get_cog("Chat")
        live_messages = 0
        if chat_cog and hasattr(chat_cog, "context_builder"):
            for channel_id, messages in chat_cog.context_builder.tracker.recent_messages.items():
                live_messages += len(messages)
                
        # 4. Long-Term Memory
        total_memories = 0
        if memory_collection is not None:
            total_memories = await memory_collection.count_documents({})
            
        embed = discord.Embed(title="AI System Diagnostics", color=discord.Color.blurple())
        embed.add_field(name="Configuration", value=f"**Provider**: {active_provider}\n**DeepSeek**: `{deepseek_model}`\n**Gemini**: `{gemini_model}`", inline=False)
        embed.add_field(name="24h Usage (ai_audit)", value=f"**Requests**: {total_requests:,}\n**Tokens**: {tokens_used:,}", inline=True)
        embed.add_field(name="Live Cache (RAM)", value=f"**Retained Msgs**: {live_messages:,}\n*(Across all channels)*", inline=True)
        embed.add_field(name="Long-Term Memory", value=f"**Semantic Vectors**: {total_memories:,}", inline=True)
        
        embed.set_footer(text="Data is refreshed when you click the button below.")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    @app_commands.command(name="sysinfo", description="View live AI usage and system diagnostics")
    async def sysinfo(self, interaction: discord.Interaction):
        if not self.is_owner(interaction.user.id):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        embed = await self.build_sysinfo_embed()
        view = SysInfoView(self, interaction)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(OwnerCog(bot))
