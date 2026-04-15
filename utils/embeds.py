import discord
from datetime import datetime
from typing import Optional

class TicketEmbeds:
    @staticmethod
    def ticket_created(ticket_type: str, user: discord.Member, opponent: Optional[str] = None) -> discord.Embed:
        embed = discord.Embed(
            title=f"Ticket Created - {ticket_type}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Created By", value=user.mention, inline=True)
        embed.add_field(name="User ID", value=str(user.id), inline=True)
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
    def ticket_log(ticket_data: dict, result_data: dict, user: discord.User) -> discord.Embed:
        embed = discord.Embed(
            title=f"Ticket Closed - {ticket_data['ticket_type']}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="User", value=f"{user.mention}\n`{user.name}`", inline=True)
        embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
        
        if ticket_data.get('opponent'):
            embed.add_field(name="Opponent", value=f"`{ticket_data['opponent']}`", inline=True)
        
        if ticket_data.get('private_link'):
            embed.add_field(name="Private Server", value=ticket_data['private_link'], inline=False)
        
        embed.add_field(name="Observer", value=f"`{result_data['observer_name']}`", inline=True)
        embed.add_field(name="Starting Rank", value=f"`{result_data['starting_rank']}`", inline=True)
        embed.add_field(name="Ending Rank", value=f"`{result_data['ending_rank']}`", inline=True)
        
        if 'winner' in result_data:
            embed.add_field(name="Winner", value=f"**{result_data['winner']}**", inline=True)
        
        if result_data.get('note'):
            embed.add_field(name="Note", value=result_data['note'], inline=False)
        
        return embed
    
    @staticmethod
    def history_embed(user: discord.Member, history: dict) -> discord.Embed:
        embed = discord.Embed(
            title=f"History for {user.display_name}",
            color=discord.Color.purple(),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"User ID: {user.id}")
        
        # Ranked 1v1 History
        if history['ranked']:
            ranked_text = ""
            for i, entry in enumerate(history['ranked'][:10], 1):
                date = entry['closed_at'][:10] if entry['closed_at'] else "Unknown"
                ranked_text += f"**#{i}** - `{date}`\n"
                ranked_text += f"  Rank: {entry['starting_rank']} -> **{entry['ending_rank']}**\n"
                ranked_text += f"  Observer: `{entry['observer_name']}`\n"
                ranked_text += f"  Winner: **{entry['winner']}**\n"
                if entry.get('opponent'):
                    ranked_text += f"  Opponent: `{entry['opponent']}`\n"
                if entry.get('note'):
                    ranked_text += f"  Note: {entry['note']}\n"
                ranked_text += "\n"
            embed.add_field(
                name=f"Ranked 1v1 Matches ({len(history['ranked'])} total, showing last 10)",
                value=ranked_text or "No matches found",
                inline=False
            )
        else:
            embed.add_field(
                name="Ranked 1v1 Matches",
                value="*No matches found*",
                inline=False
            )
        
        # Observation History
        if history['observations']:
            obs_text = ""
            for i, entry in enumerate(history['observations'][:10], 1):
                date = entry['closed_at'][:10] if entry['closed_at'] else "Unknown"
                obs_text += f"**#{i}** - `{date}`\n"
                obs_text += f"  Rank: {entry['starting_rank']} -> **{entry['ending_rank']}**\n"
                obs_text += f"  Observer: `{entry['observer_name']}`\n"
                if entry.get('note'):
                    obs_text += f"  Note: {entry['note']}\n"
                obs_text += "\n"
            embed.add_field(
                name=f"Personal Observations ({len(history['observations'])} total, showing last 10)",
                value=obs_text or "No observations found",
                inline=False
            )
        else:
            embed.add_field(
                name="Personal Observations",
                value="*No observations found*",
                inline=False
            )
        
        return embed