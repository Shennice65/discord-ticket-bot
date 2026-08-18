import discord
from discord import app_commands
from discord.ext import commands
from config import Config

class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="chat", description="Send a message to a specific channel (Owner only)")
    @app_commands.describe(message="The message to send")
    @app_commands.default_permissions(administrator=True)
    async def chat(self, interaction: discord.Interaction, message: str):
        if interaction.user.id != Config.MASTER_ADMIN_ID:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
            
        channel = self.bot.get_channel(1532367570093605005)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(1532367570093605005)
            except discord.NotFound:
                await interaction.response.send_message("Could not find the target channel.", ephemeral=True)
                return
            except discord.Forbidden:
                await interaction.response.send_message("Do not have permission to access the target channel.", ephemeral=True)
                return
            except Exception as e:
                await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
                return
            
        await channel.send(message)
        await interaction.response.send_message(f"Message sent to {channel.mention}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(OwnerCog(bot))
