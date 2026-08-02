import discord
from discord.ext import commands
from discord import app_commands
import re
from typing import List, Optional
from datetime import datetime

from database import Database
from config import Config

TIERS = ["Phantoms", "Champions", "Elites", "Legends", "Masters", "Novice"]


def is_admin_or_observer(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    observer_role = interaction.guild.get_role(Config.OBSERVER_ROLE_ID)
    if observer_role and observer_role in interaction.user.roles:
        return True
    trial_observer_role = interaction.guild.get_role(Config.TRIAL_OBSERVER_ROLE_ID)
    if trial_observer_role and trial_observer_role in interaction.user.roles:
        return True
    return False

def parse_rank(rank_str: str):
    """Returns (tier_name, number) or None if invalid."""
    match = re.match(r'^([a-zA-Z]+)\s*(\d+)$', rank_str)
    if not match:
        return None
    tier_name = match.group(1).capitalize()
    aliases = {
        "Phantom": "Phantoms",
        "Champion": "Champions",
        "Elite": "Elites",
        "Legend": "Legends",
        "Master": "Masters",
        "Novices": "Novice"
    }
    tier_name = aliases.get(tier_name, tier_name)
    number = int(match.group(2))
    return (tier_name, number)
