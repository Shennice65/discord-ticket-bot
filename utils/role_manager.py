import discord
from config import Config
from utils.ladder_utils import parse_rank

TIER_ROLE_MAP = {
    "Phantoms": "PHANTOM_ROLE_ID",
    "Champions": "CHAMPION_ROLE_ID",
    "Elites": "ELITE_ROLE_ID",
    "Legends": "LEGEND_ROLE_ID",
    "Masters": "MASTERS_ROLE_ID",
    "Novice": "NOVICE_ROLE_ID",
}

def _get_all_tier_role_ids() -> list[int]:
    return [getattr(Config, attr, 0) for attr in TIER_ROLE_MAP.values() if getattr(Config, attr, 0)]

def _get_tier_role_id(tier_name: str) -> int:
    attr = TIER_ROLE_MAP.get(tier_name)
    if not attr:
        print(f"[RoleManager] No mapping found for tier: '{tier_name}'", flush=True)
        return 0
    role_id = getattr(Config, attr, 0)
    if not role_id:
        print(f"[RoleManager] {attr} is not configured (value=0)", flush=True)
    return role_id

async def update_tier_role(guild: discord.Guild, member_or_id, new_rank_str: str) -> None:
    """Syncs a guild member's Discord tier role with their current ladder rank."""
    try:
        if isinstance(member_or_id, int):
            member = guild.get_member(member_or_id)
            if not member:
                try:
                    member = await guild.fetch_member(member_or_id)
                except (discord.NotFound, discord.HTTPException):
                    print(f"[RoleManager] Member {member_or_id} not found in guild", flush=True)
                    return
        elif isinstance(member_or_id, discord.Member):
            member = member_or_id
        elif isinstance(member_or_id, discord.User):
            member = guild.get_member(member_or_id.id)
            if not member:
                try:
                    member = await guild.fetch_member(member_or_id.id)
                except (discord.NotFound, discord.HTTPException):
                    print(f"[RoleManager] Member {member_or_id.id} not found in guild", flush=True)
                    return
        else:
            print(f"[RoleManager] Invalid member_or_id type: {type(member_or_id)}", flush=True)
            return

        new_tier_role_id = 0
        if new_rank_str and new_rank_str.lower() != "unranked":
            parsed = parse_rank(new_rank_str)
            if parsed:
                new_tier_role_id = _get_tier_role_id(parsed[0])
                print(f"[RoleManager] {member.display_name}: rank='{new_rank_str}' -> tier='{parsed[0]}' -> role_id={new_tier_role_id}", flush=True)
            else:
                print(f"[RoleManager] Could not parse rank string: '{new_rank_str}'", flush=True)
        else:
            print(f"[RoleManager] {member.display_name}: stripping all tier roles (rank='{new_rank_str}')", flush=True)

        all_tier_role_ids = _get_all_tier_role_ids()
        if not all_tier_role_ids:
            print("[RoleManager] No tier role IDs configured! Check env vars.", flush=True)
            return

        roles_to_remove = [
            role for role in member.roles
            if role.id in all_tier_role_ids and role.id != new_tier_role_id
        ]
        if roles_to_remove:
            print(f"[RoleManager] Removing roles: {[r.name for r in roles_to_remove]}", flush=True)
            await member.remove_roles(*roles_to_remove, reason="Tier role update")

        if new_tier_role_id:
            new_role = guild.get_role(new_tier_role_id)
            if not new_role:
                print(f"[RoleManager] Role ID {new_tier_role_id} not found in guild!", flush=True)
                return
            if new_role not in member.roles:
                print(f"[RoleManager] Adding role '{new_role.name}' to {member.display_name}", flush=True)
                await member.add_roles(new_role, reason="Tier role update")
            else:
                print(f"[RoleManager] {member.display_name} already has role '{new_role.name}'", flush=True)

    except discord.Forbidden:
        print(f"[RoleManager] Missing permissions to update roles for {member_or_id}", flush=True)
    except Exception as e:
        print(f"[RoleManager] Error updating tier role for {member_or_id}: {e}", flush=True)
