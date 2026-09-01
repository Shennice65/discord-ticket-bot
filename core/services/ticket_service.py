import discord
from typing import Tuple, Optional, Dict, Any

class TicketService:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db

    async def validate_ranked_request(self, user_id: int, opponent_id: int) -> Tuple[bool, str, bool]:
        if opponent_id == user_id:
            return False, "You cannot 1v1 yourself!", False
            
        can_r1, r1_reason = await self.db.can_player_r1(user_id)
        if not can_r1:
            return False, r1_reason, False
            
        idx_user = await self.db.get_global_rank_index(user_id)
        idx_opp = await self.db.get_global_rank_index(opponent_id)
        
        if idx_user == -1:
            return False, "You cannot request a ranked 1v1 while you are unranked!", False
            
        if idx_opp == -1:
            return False, "You cannot request a ranked 1v1 against an unranked player!", False
            
        is_out_of_range = abs(idx_user - idx_opp) > 5
            
        cooldown = await self.db.get_ranked_cooldown(user_id)
        if cooldown > 0:
            hours = int(cooldown)
            minutes = int((cooldown - hours) * 60)
            return False, f"You can only request one ranked match per day! Please wait **{hours}h {minutes}m**.", False
            
        rematch_cd = await self.db.get_rematch_cooldown(user_id, opponent_id)
        if rematch_cd > 0:
            hours = int(rematch_cd)
            minutes = int((rematch_cd - hours) * 60)
            return False, f"You must wait **{hours}h {minutes}m** before facing <@{opponent_id}> again!", False
            
        return True, "", is_out_of_range

    async def validate_observation_request(self, user_id: int) -> Tuple[bool, str]:
        cooldown = await self.db.get_obs_cooldown(user_id)
        if cooldown > 0:
            days = int(cooldown)
            remainder_hours = (cooldown - days) * 24
            hours = int(remainder_hours)
            minutes = int((remainder_hours - hours) * 60)
            return False, f"You can only request a personal observation once every two weeks! Please wait **{days}d {hours}h {minutes}m**."
            
        unrank_cooldown = await self.db.get_unrank_cooldown(user_id)
        is_self_unranked = await self.db.is_player_self_unranked(user_id)
        if is_self_unranked and unrank_cooldown > 0:
            d = int(unrank_cooldown)
            remainder_hours = (unrank_cooldown - d) * 24
            h = int(remainder_hours)
            m = int((remainder_hours - h) * 60)
            return False, f"You cannot request a Personal Observation while your unrank cooldown is active! Please wait **{d}d {h}h {m}m**."
            
        return True, ""

    async def check_and_notify_rank_change(self, user_id: int, new_rank: str) -> None:
        is_unranked = not new_rank or new_rank.lower() == "unranked"
        cursor = self.db.tickets.find({
            "status": {"$in": ["open", "pending_accept"]} if is_unranked else "open",
            "ticket_type": "Ranked 1v1", 
            "$or": [{"user_id": user_id}, {"opponent_id": user_id}]
        })
        open_tickets = await cursor.to_list(length=None)
        
        for ticket in open_tickets:
            channel = self.bot.get_channel(ticket['channel_id'])

            if is_unranked:
                closed = await self.db.close_ticket(ticket['channel_id'], user_id)
                if not closed or not channel:
                    continue

                await channel.send(
                    f"This ranked ticket has been automatically cancelled because <@{user_id}> is now **Unranked**."
                )
                await channel.delete(reason="Player unranked")
                continue

            if not channel:
                continue
                
            other_id = ticket['opponent_id'] if ticket['user_id'] == user_id else ticket['user_id']
            if not other_id:
                continue
                
            idx1 = await self.db.get_global_rank_index(user_id)
            idx2 = await self.db.get_global_rank_index(other_id)
            
            out_of_range = False
            if idx1 != -1 and idx2 != -1:
                out_of_range = abs(idx1 - idx2) > 5
                
            msg = f"<@{user_id}>'s rank has been updated to **{new_rank}**!\n"
            if out_of_range:
                msg += "⚠️ **Warning:** This matchup is now outside the allowed 5-rank range. You may decide whether to continue or cancel this ticket."
                
            await channel.send(msg)
