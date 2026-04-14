import discord
from datetime import datetime
from typing import Optional

class TicketEmbeds:
    @staticmethod
    def ticket_created(ticket_type: str, user: discord.Member, opponent: Optional[str] = None) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎫 {ticket_type} Ticket Created",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Created By", value=user.mention, inline=True)
        embed.add_field(name="User ID", value=user.id, inline=True)
        if opponent:
            embed.add_field(name="Opponent", value=opponent, inline=True)
        embed.add_field(
            name="Instructions",
            value="An observer will assist you shortly.\n"
                  "Use `/close` when the session is complete.",
            inline=False
        )
        embed.set_footer(text=f"User: {user.name}")
        return embed
    
    @staticmethod
    def ticket_log(ticket_data: dict, result_data: dict, user: discord.Member) -> discord.Embed:
        embed = discord.Embed(
            title=f"📋 Ticket Closed - {ticket_data['ticket_type']}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="User ID", value=user.id, inline=True)
        
        if ticket_data.get('opponent'):
            embed.add_field(name="Opponent", value=ticket_data['opponent'], inline=True)
        
        embed.add_field(name="Observer", value=result_data['observer_name'], inline=True)
        embed.add_field(name="Starting Rank", value=result_data['starting_rank'], inline=True)
        embed.add_field(name="Ending Rank", value=result_data['ending_rank'], inline=True)
        
        if 'winner' in result_data:
            embed.add_field(name="Winner", value=result_data['winner'], inline=True)
        
        if result_data.get('optional_note'):
            embed.add_field(name="Note", value=result_data['optional_note'], inline=False)
        
        return embed
    
    @staticmethod
    def history_embed(user: discord.Member, history: dict) -> discord.Embed:
        embed = discord.Embed(
            title=f"📊 History for {user.name}",
            color=discord.Color.purple(),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        
        if history['ranked']:
            ranked_text = ""
            for entry in history['ranked'][:5]:
                date = entry['closed_at'][:10] if entry['closed_at'] else "Unknown"
                ranked_text += f"**{date}** - {entry['starting_rank']} → {entry['ending_rank']}\n"
                ranked_text += f"Observer: {entry['observer_name']} | Winner: {entry['winner']}\n\n"
            embed.add_field(name="🎮 Ranked 1v1 Matches", value=ranked_text or "None", inline=False)
        else:
            embed.add_field(name="🎮 Ranked 1v1 Matches", value="No matches found", inline=False)
        
        if history['observations']:
            obs_text = ""
            for entry in history['observations'][:5]:
                date = entry['closed_at'][:10] if entry['closed_at'] else "Unknown"
                obs_text += f"**{date}** - {entry['starting_rank']} → {entry['ending_rank']}\n"
                obs_text += f"Observer: {entry['observer_name']}\n"
                if entry.get('optional_note'):
                    obs_text += f"Note: {entry['optional_note']}\n"
                obs_text += "\n"
            embed.add_field(name="👁️ Personal Observations", value=obs_text or "None", inline=False)
        else:
            embed.add_field(name="👁️ Personal Observations", value="No observations found", inline=False)
        
        embed.set_footer(text=f"Showing last 5 entries of each type")
        return embed