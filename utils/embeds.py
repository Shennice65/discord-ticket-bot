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
                
                if entry.get('opponent_id') == user.id:
                    # User was the opponent, so their opponent is the requester
                    actual_opponent = f"<@{entry['user_id']}>"
                else:
                    # User was the requester, so their opponent is the ticket opponent
                    actual_opponent = f"`{entry.get('opponent_name')}`" if entry.get('opponent_name') else "Unknown"
                    
                desc += f"> **Opponent:** {actual_opponent}\n"
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

    @staticmethod
    def h2h_embed(player1: discord.Member, player2: discord.Member, h2h_data: dict) -> discord.Embed:
        """Head-to-head stats embed between two players."""
        embed = discord.Embed(
            title=f"⚔️ Head-to-Head: {player1.display_name} vs {player2.display_name}",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        
        total = h2h_data["total"]
        p1_wins = h2h_data["p1_wins"]
        p2_wins = h2h_data["p2_wins"]
        
        if total == 0:
            embed.description = "*No matches found between these players.*"
            return embed
        
        # Win rate visual bar
        bar_length = 20
        p1_blocks = round((p1_wins / total) * bar_length) if total > 0 else 0
        p2_blocks = bar_length - p1_blocks
        bar = "🟦" * p1_blocks + "🟥" * p2_blocks
        
        p1_rate = (p1_wins / total) * 100
        p2_rate = (p2_wins / total) * 100
        
        embed.add_field(
            name="Overall Record",
            value=(
                f"**{player1.display_name}**: {p1_wins}W ({p1_rate:.0f}%)\n"
                f"**{player2.display_name}**: {p2_wins}W ({p2_rate:.0f}%)\n"
                f"**Total Matches**: {total}\n\n"
                f"{bar}"
            ),
            inline=False
        )
        
        # Recent matches
        recent = h2h_data.get("recent_matches", [])
        if recent:
            for i, entry in enumerate(recent[:5], 1):
                date = entry['closed_at'][:10] if entry.get('closed_at') else "Unknown"
                winner_id = entry.get('winner_id')
                
                if winner_id == player1.id:
                    result_text = f"**{player1.display_name}** won"
                elif winner_id == player2.id:
                    result_text = f"**{player2.display_name}** won"
                else:
                    result_text = "Unknown"
                
                desc = f"> {result_text}\n"
                
                w_old = entry.get('winner_old') or 'Unranked'
                w_new = entry.get('winner_new') or 'Unranked'
                l_old = entry.get('loser_old') or 'Unranked'
                l_new = entry.get('loser_new') or 'Unranked'
                desc += f"> **Winner:** `{w_old}` ➔ `{w_new}` | **Loser:** `{l_old}` ➔ `{l_new}`\n"
                
                if entry.get('observer_name'):
                    desc += f"> **Observer:** `{entry['observer_name']}`\n"
                
                embed.add_field(name=f"Match #{i} — {date}", value=desc, inline=False)
        
        embed.set_footer(text=f"{player1.name} vs {player2.name}")
        return embed