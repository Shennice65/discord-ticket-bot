import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import os
import re
from datetime import datetime
from typing import Optional, List

from config import Config
from database import Database
from utils.embeds import TicketEmbeds


def validate_and_format_rank(rank_str: str) -> Optional[str]:
    tiers = {
        "novice": "Novices", "novices": "Novices",
        "master": "Masters", "masters": "Masters",
        "legend": "Legends", "legends": "Legends",
        "elite": "Elites", "elites": "Elites",
        "champion": "Champions", "champions": "Champions",
        "phantom": "Phantoms", "phantoms": "Phantoms"
    }
    if rank_str.strip().lower() == "unranked":
        return "Unranked"
    match = re.match(r'^\s*([a-zA-Z]+)\s*(\d+)\s*$', rank_str)
    if not match:
        return None
    tier_input = match.group(1).lower()
    number = match.group(2)
    if tier_input in tiers:
        return f"{tiers[tier_input]} {number}"
    return None
def get_observer_mention(guild: discord.Guild) -> str:
    mentions = []
    observer_role = guild.get_role(Config.OBSERVER_ROLE_ID)
    if observer_role:
        mentions.append(observer_role.mention)
    if hasattr(Config, 'TRIAL_OBSERVER_ROLE_ID') and Config.TRIAL_OBSERVER_ROLE_ID:
        trial_role = guild.get_role(Config.TRIAL_OBSERVER_ROLE_ID)
        if trial_role:
            mentions.append(trial_role.mention)
    if not mentions:
        mentions.append("@Observers")
    return " ".join(mentions)


def is_observer_or_trial(member: discord.Member, ticket_type: str = None) -> bool:
    observer_role = member.guild.get_role(Config.OBSERVER_ROLE_ID)
    trial_role = None
    if hasattr(Config, 'TRIAL_OBSERVER_ROLE_ID') and Config.TRIAL_OBSERVER_ROLE_ID:
        trial_role = member.guild.get_role(Config.TRIAL_OBSERVER_ROLE_ID)
    no_obs_role = None
    if hasattr(Config, 'NO_PERSONAL_OBS_ROLE_ID') and Config.NO_PERSONAL_OBS_ROLE_ID:
        no_obs_role = member.guild.get_role(Config.NO_PERSONAL_OBS_ROLE_ID)
    
    has_no_obs = no_obs_role and no_obs_role in member.roles
    
    is_obs = (observer_role and observer_role in member.roles) or (trial_role and trial_role in member.roles)
    
    if not is_obs:
        return False
    
    if ticket_type and "obs" in ticket_type.lower() and has_no_obs:
        return False
    
    if ticket_type and "personal" in ticket_type.lower() and has_no_obs:
        return False
    
    return True


def get_observer_overwrites(guild: discord.Guild, base_overwrites: dict, ticket_type: str = None) -> dict:
    overwrites = base_overwrites.copy()
    observer_role = guild.get_role(Config.OBSERVER_ROLE_ID)
    if observer_role:
        overwrites[observer_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    if hasattr(Config, 'TRIAL_OBSERVER_ROLE_ID') and Config.TRIAL_OBSERVER_ROLE_ID:
        trial_role = guild.get_role(Config.TRIAL_OBSERVER_ROLE_ID)
        if trial_role:
            overwrites[trial_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    if ticket_type and "obs" in ticket_type.lower() and hasattr(Config, 'NO_PERSONAL_OBS_ROLE_ID') and Config.NO_PERSONAL_OBS_ROLE_ID:
        no_obs_role = guild.get_role(Config.NO_PERSONAL_OBS_ROLE_ID)
        if no_obs_role:
            overwrites[no_obs_role] = discord.PermissionOverwrite(read_messages=False, send_messages=False)
    return overwrites
