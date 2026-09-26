import discord
from discord.ext import commands
from discord import app_commands
from ai.engagement import UserEngagementScorer

class EngagementCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scorer = UserEngagementScorer(bot)

    @app_commands.command(name="vibecheck", description="See what the AI thinks of your chat style!")
    async def vibecheck(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        fingerprint = await self.scorer._get_fingerprint(interaction.user.id)
        profile = await self.scorer.get_profile(interaction.user.id, interaction.user)
        
        embed = discord.Embed(
            title=f"Vibe Check: {interaction.user.display_name}",
            color=discord.Color.purple()
        )
        
        embed.add_field(name="Engagement Tier", value=profile.tier.capitalize(), inline=True)
        embed.add_field(name="Engagement Score", value=str(profile.score), inline=True)
        
        style_desc = []
        if fingerprint.caps_style == "lowercase":
            style_desc.append("• You type mostly in lowercase (too cool for the shift key).")
        elif fingerprint.caps_style == "uppercase":
            style_desc.append("• YOU YELL A LOT.")
        else:
            style_desc.append("• Normal capitalization.")
            
        if fingerprint.punctuation == "formal":
            style_desc.append("• You use periods. Very formal.")
        elif fingerprint.punctuation == "expressive":
            style_desc.append("• Very expressive punctuation!?!")
            
        length_map = {"short": "Keep it brief", "medium": "Average length", "long": "You write essays"}
        style_desc.append(f"• {length_map.get(fingerprint.avg_length, 'Average length')}.")
        
        if fingerprint.emoji_heavy:
            style_desc.append("• You love emojis 💯🔥")
            
        if fingerprint.slang_markers:
            style_desc.append(f"• Favorite slang: {', '.join(fingerprint.slang_markers)}")
            
        embed.add_field(name="AI's Notes on your Style", value="\n".join(style_desc) or "No distinct style detected yet.", inline=False)
        
        if profile.style_hint:
            embed.set_footer(text=f"AI Prompt modifier: {profile.style_hint}")
            
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(EngagementCommands(bot))
