import discord
from typing import Tuple, Optional, Dict, Any

class TicketService:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db

    async def validate_ranked_request(self, user_id: int, opponent_id: int, check_cooldowns: bool = True) -> Tuple[bool, str]:
        if opponent_id == user_id:
            return False, "You cannot 1v1 yourself!"
            
        indexes = await self.db.get_global_rank_indexes([user_id, opponent_id])
        idx_user = indexes.get(user_id, -1)
        idx_opp = indexes.get(opponent_id, -1)
        
        if idx_user == -1:
            return False, "You cannot request a ranked 1v1 while you are unranked!"
            
        if idx_opp == -1:
            return False, "You cannot request a ranked 1v1 against an unranked player!"
            
        user_rank_str = await self.db.get_player_rank(user_id)
        opp_rank_str = await self.db.get_player_rank(opponent_id)
        
        from utils.ladder_utils import parse_rank
        user_parsed = parse_rank(user_rank_str) if user_rank_str else None
        opp_parsed = parse_rank(opp_rank_str) if opp_rank_str else None
        
        limit = 5
        if user_parsed:
            user_tier, user_num = user_parsed
            if user_tier in ["Masters", "Novices", "Novice"] or (user_tier == "Legends" and user_num >= 9):
                limit = 7
                
        gap = abs(idx_user - idx_opp)
        if gap > limit:
            return False, f"You cannot challenge someone more than **{limit} ranks** away from you."
            
        if user_parsed and opp_parsed:
            user_tier = user_parsed[0]
            opp_tier = opp_parsed[0]
            
            if user_tier in ["Elites", "Champions"] and opp_tier in ["Champions", "Phantoms"]:
                tiers_order = {"Phantoms": 0, "Champions": 1, "Elites": 2, "Legends": 3, "Masters": 4, "Novice": 5, "Novices": 5}
                if tiers_order.get(opp_tier, 99) < tiers_order.get(user_tier, 99):
                    return False, f"**{user_tier}** cannot challenge **{opp_tier}** in Ranked 1v1. Request a **Personal Observation** instead."
            
        if check_cooldowns:
            cooldown = await self.db.get_ranked_cooldown(user_id)
            if cooldown > 0:
                hours = int(cooldown)
                minutes = int((cooldown - hours) * 60)
                return False, f"You can only request one ranked match per day! Please wait **{hours}h {minutes}m**."
                
            rematch_cd = await self.db.get_rematch_cooldown(user_id, opponent_id)
            if rematch_cd > 0:
                days = int(rematch_cd / 24)
                hours = int(rematch_cd % 24)
                minutes = int((rematch_cd * 60) % 60)
                
                time_str = ""
                if days > 0:
                    time_str += f"{days}d "
                time_str += f"{hours}h {minutes}m"
                
                return False, f"You must wait **{time_str.strip()}** before facing <@{opponent_id}> again!"
            
        return True, ""

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
                
            msg = f"<@{user_id}>'s rank has been updated to **{new_rank}**!\n"
                
            await channel.send(msg)
