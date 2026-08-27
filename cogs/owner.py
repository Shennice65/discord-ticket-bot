import discord
from discord import app_commands
from discord.ext import commands
from config import Config

class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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

async def setup(bot):
    await bot.add_cog(OwnerCog(bot))
