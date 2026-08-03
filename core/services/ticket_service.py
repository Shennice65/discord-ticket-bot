import discord
from typing import Tuple, Optional, Dict, Any

class TicketService:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db

    async def validate_ranked_request(self, user_id: int, opponent_id: int) -> Tuple[bool, str, bool]:
        """
        Validates if a ranked ticket can be created.
        Returns: (is_valid, error_message, is_out_of_range)
        """
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
            
        is_out_of_range = False
        if abs(idx_user - idx_opp) > 5:
            is_out_of_range = True
            
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
        """
        Validates if an observation ticket can be created.
        Returns: (is_valid, error_message)
        """
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
