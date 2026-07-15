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
        
        if ticket_data.get('opponent_name'):
            embed.add_field(name="Opponent", value=f"`{ticket_data['opponent_name']}`", inline=True)
        
        if ticket_data.get('private_link'):
            embed.add_field(name="Private Server", value=ticket_data['private_link'], inline=False)
        
        embed.add_field(name="Observer", value=f"`{result_data['observer_name']}`", inline=True)
        if ticket_data['ticket_type'] == "Ranked 1v1":
            w_old = result_data.get('winner_old') or 'Unranked'
            w_new = result_data.get('winner_new') or 'Unranked'
            l_old = result_data.get('loser_old') or 'Unranked'
            l_new = result_data.get('loser_new') or 'Unranked'
            embed.add_field(name="Rank Changes", value=f"> **Winner:** `{w_old}` ➔ `{w_new}`\n> **Loser:** `{l_old}` ➔ `{l_new}`", inline=False)
        else:
            embed.add_field(name="Rank Change", value=f"> `{result_data.get('starting_rank', 'Unranked')}` ➔ `{result_data.get('ending_rank', 'Unranked')}`", inline=False)
            
        if 'winner' in result_data:
            embed.add_field(name="Winner", value=f"**{result_data['winner']}**", inline=True)
        
        if result_data.get('note'):
            embed.add_field(name="Note", value=result_data['note'], inline=False)
        
        return embed
    
    @staticmethod
    def _base_embed(user: discord.Member, title: str, color: discord.Color = discord.Color.purple()) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"User ID: {user.id}")
        return embed

    @staticmethod
    def history_overview_embed(user: discord.Member, history: dict, unrank_info: dict = None) -> discord.Embed:
        embed = TicketEmbeds._base_embed(
            user, 
            f"History Overview for {user.display_name}", 
            discord.Color.red() if unrank_info else discord.Color.purple()
        )
        
        # Unranked badge
        if unrank_info:
            days_left = int(unrank_info["cooldown_days"])
            status = f"UNRANKED — Was **{unrank_info['original_rank']}**"
            if days_left > 0:
                status += f"\nRe-rank locked for **{days_left} more days**"
                status += f"\nR1s blocked until back to **{unrank_info['original_rank']}**"
            else:
                status += f"\nRe-rank cooldown expired"
            embed.add_field(
                name="UNRANKED PLAYER",
                value=status,
                inline=False
            )
        
        # Calculate Stats
        total_matches = len(history['ranked'])
        if total_matches > 0:
            wins = 0
            for entry in history['ranked']:
                if 'winner_id' in entry and entry['winner_id'] is not None:
                    if entry['winner_id'] == user.id:
                        wins += 1
                else:
                    w_str = entry.get('winner', '').lower()
                    if w_str == user.name.lower() or w_str in user.name.lower():
                        wins += 1
            
            losses = total_matches - wins
            win_rate = (wins / total_matches) * 100
            
            embed.add_field(
                name="Ranked Stats Overview",
                value=f"**Total Matches**: {total_matches}\n**Wins**: {wins} | **Losses**: {losses}\n**Win Rate**: {win_rate:.1f}%",
                inline=False
            )
        else:
            embed.add_field(name="Ranked Stats Overview", value="*No matches recorded yet.*", inline=False)
            
        total_obs = len(history['observations'])
        embed.add_field(name="Personal Observations", value=f"**Total Observations**: {total_obs}", inline=False)
        
        return embed

    @staticmethod
    def history_ranked_embed(user: discord.Member, history: dict) -> discord.Embed:
        embed = TicketEmbeds._base_embed(user, f"Ranked Matches for {user.display_name}")
        
        if history['ranked']:
            for i, entry in enumerate(history['ranked'][:10], 1):
                date = entry['closed_at'][:10] if entry['closed_at'] else "Unknown"
                
                if 'winner_id' in entry and entry['winner_id'] is not None:
                    is_win = (entry['winner_id'] == user.id)
                else:
                    winner_str = entry.get('winner', '').lower()
                    is_win = (winner_str == user.name.lower() or winner_str in user.name.lower())
                    
                result_text = "WON" if is_win else "LOST"
                
                desc = f"> **Result:** **{result_text}**\n"
                if is_win:
                    start_rank = entry.get('winner_old') or entry.get('starting_rank') or "Unranked"
                    end_rank = entry.get('winner_new') or entry.get('ending_rank') or "Unranked"
                else:
                    start_rank = entry.get('loser_old') or entry.get('starting_rank') or "Unranked"
                    end_rank = entry.get('loser_new') or entry.get('ending_rank') or "Unranked"
                    
                desc += f"> **Rank Change:** `{start_rank}` ➔ `{end_rank}`\n"
                
                if entry.get('opponent_name'):
                    desc += f"> **Opponent:** `{entry['opponent_name']}`\n"
                desc += f"> **Observer:** `{entry['observer_name']}`\n"
                if entry.get('note'):
                    desc += f"> **Note:** {entry['note']}\n"
                
                embed.add_field(name=f"Match #{i} — {date}", value=desc, inline=False)
        else:
            embed.add_field(name="Ranked Matches", value="*No matches found*", inline=False)
            
        return embed

    @staticmethod
    def history_observation_embed(user: discord.Member, history: dict) -> discord.Embed:
        embed = TicketEmbeds._base_embed(user, f"Observations for {user.display_name}")
        
        if history['observations']:
            for i, entry in enumerate(history['observations'][:10], 1):
                date = entry['closed_at'][:10] if entry['closed_at'] else "Unknown"
                
                start_rank = entry.get('starting_rank') or "Unranked"
                end_rank = entry.get('ending_rank') or "Unranked"
                desc = f"> **Rank Change:** `{start_rank}` ➔ `{end_rank}`\n"
                desc += f"> **Observer:** `{entry['observer_name']}`\n"
                if entry.get('note'):
                    desc += f"> **Note:** {entry['note']}\n"
                
                embed.add_field(name=f"Observation #{i} — {date}", value=desc, inline=False)
        else:
            embed.add_field(name="Observations", value="*No observations found*", inline=False)
            
        return embed