import os
import discord
from datetime import datetime
from typing import Optional, Tuple
from utils.ranking_utils import parse_rank

class TicketEmbeds:
    @staticmethod
    def create_ranked_1v1_ticket_embed(user: discord.Member, opponent_name: str,
                                       u_rank: str, o_rank: str,
                                       u_rate: float, o_rate: float) -> Tuple[discord.Embed, Optional[discord.File]]:
        """Creates the sleek 3-column Ranked 1v1 lobby ticket embed."""
        embed = discord.Embed(
            color=discord.Color(0x2b2d31),
            timestamp=datetime.utcnow()
        )
        avatar_url = user.display_avatar.url if hasattr(user, 'display_avatar') and user.display_avatar else None
        embed.set_author(name=f"{user.display_name} VS {opponent_name}", icon_url=avatar_url)

        # Truncate names if excessively long
        p1_name = user.display_name[:15]
        p2_name = opponent_name[:15]
        p1_rate = f"{u_rate:.1f}%"
        p2_rate = f"{o_rate:.1f}%"

        # Use Discord native blockquotes inside the Field Value to create 
        # the unselectable, continuous grey vertical UI bar.
        embed.add_field(
            name="\u200B", 
            value=f"> [Player]\n> **{p1_name}**\n> **{p2_name}**", 
            inline=True
        )
        embed.add_field(
            name="\u200B", 
            value=f"> [Rank]\n> **{u_rank}**\n> **{o_rank}**", 
            inline=True
        )
        embed.add_field(
            name="\u200B", 
            value=f"> [Winrate]\n> **{p1_rate}**\n> **{p2_rate}**", 
            inline=True
        )

        embed.set_footer(text="Wait for an observer to referee your match before starting.")

        # Resolve tier thumbnail from challenger's rank
        tier_file = None
        parsed = parse_rank(u_rank)
        if parsed:
            tier_name = parsed[0].lower()
            tier_path = os.path.join("assets", "tiers", f"{tier_name}.png")
            if os.path.exists(tier_path):
                tier_file = discord.File(tier_path, filename="tier.png")
                embed.set_thumbnail(url="attachment://tier.png")

        return embed, tier_file

    @staticmethod
    def ticket_created(ticket_type: str, user: discord.Member, opponent: Optional[str] = None,
                       user_stats: Optional[str] = None, opp_stats: Optional[str] = None) -> discord.Embed:
        embed = discord.Embed(
            title=f"Ticket Created - {ticket_type}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        
        if ticket_type == "Ranked 1v1" and opponent and user_stats and opp_stats:
            embed.description = f"**{user.display_name}** `VS` **{opponent}**"
            
            u_clean = user_stats.replace("**Total Matches**:", "**Matches**:").replace("**Win Rate**:", "**WR**:")
            o_clean = opp_stats.replace("**Total Matches**:", "**Matches**:").replace("**Win Rate**:", "**WR**:")
            
            val = f"**{user.display_name}**\n{u_clean}\n\n**{opponent}**\n{o_clean}"
            embed.add_field(name="Matchup", value=val, inline=False)
        else:
            user_val = user.display_name
            if user_stats:
                user_val += f"\n\n{user_stats}"
            embed.add_field(name="Created By", value=user_val, inline=True)
            
            if opponent:
                opp_val = opponent
                if opp_stats:
                    opp_val += f"\n\n{opp_stats}"
                embed.add_field(name="Opponent", value=opp_val, inline=True)
            
        if ticket_type == "Ranked 1v1":
            instructions = (
                "- An observer will hop in to ref your match\n"
                "- Play it out fair and square — no dodging, no throwing\n"
                "- Once it's done, the observer calls the winner\n"
                "- Both players' ranks get updated after that\n\n"
                "Sit tight and wait for an observer before you start."
            )
        else:
            instructions = (
                "- An observer will drop in to watch you play\n"
                "- Show them what you got — they're sizing up your skill level\n"
                "- After they've seen enough, they'll set or adjust your rank\n"
                "- Your rank can go up, down, or stay the same depending on how you perform\n\n"
                "Hang tight and wait for an observer before you start."
            )
        
        embed.add_field(
            name="How It Works",
            value=instructions,
            inline=False
        )
        embed.set_footer(text=f"User: {user.name}")
        return embed
    
    @staticmethod
    def calculate_ranked_stats(user_id: int, user_name: str, history: dict) -> tuple[int, int, int, float]:
        """Returns (total_matches, wins, losses, win_rate)"""
        total_matches = len(history.get('ranked', []))
        if total_matches == 0:
            return 0, 0, 0, 0.0
            
        wins = 0
        for entry in history['ranked']:
            if 'winner_id' in entry and entry['winner_id'] is not None:
                if entry['winner_id'] == user_id:
                    wins += 1
            else:
                w_str = entry.get('winner', '').lower()
                if w_str == user_name.lower():
                    wins += 1
        
        losses = total_matches - wins
        win_rate = (wins / total_matches) * 100
        return total_matches, wins, losses, win_rate
    
    @staticmethod
    def ticket_log(ticket_data: dict, result_data: dict, user: discord.User) -> discord.Embed:
        embed = discord.Embed(
            title=f"Ticket Closed - {ticket_data['ticket_type']}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="User", value=f"{user.mention}\n`{user.name}`", inline=True)
        
        if ticket_data.get('opponent_name'):
            opponent_value = f"<@{ticket_data['opponent_id']}>\n`{ticket_data['opponent_name']}`" if ticket_data.get('opponent_id') else f"`{ticket_data['opponent_name']}`"
            embed.add_field(name="Opponent", value=opponent_value, inline=True)
        

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
    def history_overview_embed(user: discord.Member, history: dict, unrank_info: dict = None, obs_cooldown_days: float = 0.0, ranked_cooldown_days: float = 0.0, current_rank: str = "Unranked") -> discord.Embed:
        embed = TicketEmbeds._base_embed(
            user, 
            f"History Overview for {user.display_name}", 
            discord.Color.red() if unrank_info else discord.Color.purple()
        )
        
        # Current Rank
        embed.add_field(name="Current Rank", value=f"**{current_rank}**", inline=False)
        
        # Unranked badge
        if unrank_info:
            unrank_cd = float(unrank_info["cooldown_days"])
            d = int(unrank_cd)
            remainder_hours = (unrank_cd - d) * 24
            h = int(remainder_hours)
            m = int((remainder_hours - h) * 60)
            
            status = f"UNRANKED — Was **{unrank_info['original_rank']}**"
            if unrank_cd > 0:
                status += f"\nRe-rank locked for **{d}d {h}h {m}m**"
                status += f"\nR1s blocked until back to **{unrank_info['original_rank']}**"
            else:
                status += f"\nRe-rank cooldown expired"
            embed.add_field(
                name="UNRANKED PLAYER",
                value=status,
                inline=False
            )
        
        # Calculate Stats
        total_matches, wins, losses, win_rate = TicketEmbeds.calculate_ranked_stats(user.id, user.name, history)
        if total_matches > 0:
            embed.add_field(
                name="Ranked Stats Overview",
                value=f"**Total Matches**: {total_matches}\n**Wins**: {wins} | **Losses**: {losses}\n**Win Rate**: {win_rate:.1f}%",
                inline=False
            )
        else:
            embed.add_field(name="Ranked Stats Overview", value="*No matches recorded yet.*", inline=False)
            
        total_obs = len(history['observations'])
        embed.add_field(name="Personal Observations", value=f"**Total Observations**: {total_obs}", inline=False)
        
        def format_cd(cd_days):
            if cd_days <= 0:
                return "<:check:1537515644109725716>"
            d = int(cd_days)
            rem_h = (cd_days - d) * 24
            h = int(rem_h)
            m = int((rem_h - h) * 60)
            return f"{d}d {h}h {m}m"

        ranked_status = format_cd(ranked_cooldown_days)
        obs_status = format_cd(obs_cooldown_days)

        if unrank_info:
            ranked_status = "<:locke:1537533192343396483> Blocked (Unranked)"
            if float(unrank_info["cooldown_days"]) > 0:
                unrank_cd_str = format_cd(float(unrank_info["cooldown_days"]))
                obs_status = f"<:locke:1537533192343396483> Blocked ({unrank_cd_str})"
            
        embed.add_field(
            name="COOLDOWN",
            value=f"**Ranked**: {ranked_status}\n**Personal Obs**: {obs_status}",
            inline=False
        )
        
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
                    is_win = (winner_str == user.name.lower())
                    
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
                    requester = user.guild.get_member(entry['user_id']) if hasattr(user, 'guild') and user.guild else None
                    if requester:
                        actual_opponent = f"`{requester.display_name}`"
                    else:
                        actual_opponent = f"`User ID: {entry['user_id']}`"
                else:
                    # User was the requester, so their opponent is the ticket opponent
                    actual_opponent = f"`{entry.get('opponent_name')}`" if entry.get('opponent_name') else "`Unknown`"
                    
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

    @staticmethod
    def winrate_leaderboard_embed(entries: list[tuple[discord.Member | discord.User | str, dict]], min_matches: int) -> discord.Embed:
        """Create the winrate leaderboard embed."""
        embed = discord.Embed(
            title="📈 Top Winrate Leaderboard",
            description=f"*Top 10 players by winrate (Minimum {min_matches} matches)*\n\n",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        
        if not entries:
            embed.description += "*No players found matching the criteria.*"
            return embed
            
        medal_emojis = ["<:gold:1537886239674339472>", "<:silver:1537886287367766047>", "<:bronze:1537886327788535838>"]
        
        for i, (user_name_or_mention, stats) in enumerate(entries, 1):
            rank = f"{medal_emojis[i-1]} `#{i}`" if i <= 3 else f"**` {i} `**"
            win_rate = stats.get('win_rate', 0)
            wins = stats.get('wins', 0)
            losses = stats.get('losses', 0)
            matches = stats.get('matches', 0)
            
            embed.description += f"{rank} {user_name_or_mention} — **{win_rate:.1f}%** ({wins}W / {losses}L / {matches}M)\n"
            
        embed.set_footer(text="Ranked 1v1 Matches Only")
        return embed
