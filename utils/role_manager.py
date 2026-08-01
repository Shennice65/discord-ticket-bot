import discord
from config import Config
from ladder_utils import parse_rank

# Map each tier name to its Config role ID attribute
TIER_ROLE_MAP = {
    "Phantoms": "PHANTOM_ROLE_ID",
    "Champions": "CHAMPION_ROLE_ID",
    "Elites": "ELITE_ROLE_ID",
    "Legends": "LEGEND_ROLE_ID",
    "Masters": "MASTERS_ROLE_ID",
    "Novice": "NOVICE_ROLE_ID",
}


def _get_all_tier_role_ids() -> list[int]:
    """Return a list of all configured tier role IDs (skipping unconfigured ones)."""
    ids = []
    for attr in TIER_ROLE_MAP.values():
        role_id = getattr(Config, attr, 0)
        if role_id:
            ids.append(role_id)
    return ids


def _get_tier_role_id(tier_name: str) -> int:
    """Return the role ID for a given tier name, or 0 if not configured."""
    attr = TIER_ROLE_MAP.get(tier_name)
    if not attr:
        print(f"[RoleManager] No mapping found for tier: '{tier_name}'")
        return 0
    role_id = getattr(Config, attr, 0)
    if not role_id:
        print(f"[RoleManager] {attr} is not configured (value=0)")
    return role_id


async def update_tier_role(guild: discord.Guild, member_or_id, new_rank_str: str) -> None:
    """Update a member's tier role based on their new rank string.
    
    - Removes all existing tier roles from the member.
    - Adds the role matching the new tier (if any).
    - Logs errors to console for debugging.
    
    Args:
        guild: The Discord guild.
        member_or_id: A discord.Member, discord.User, or an int user ID.
        new_rank_str: The new rank string (e.g. "Legends 3") or empty/"Unranked" to strip all.
    """
    try:
        # Resolve member
        if isinstance(member_or_id, int):
            member = guild.get_member(member_or_id)
            if not member:
                try:
                    member = await guild.fetch_member(member_or_id)
                except (discord.NotFound, discord.HTTPException):
                    print(f"[RoleManager] Member {member_or_id} not found in guild")
                    return
        elif isinstance(member_or_id, discord.Member):
            member = member_or_id
        elif isinstance(member_or_id, discord.User):
            member = guild.get_member(member_or_id.id)
            if not member:
                try:
                    member = await guild.fetch_member(member_or_id.id)
                except (discord.NotFound, discord.HTTPException):
                    print(f"[RoleManager] Member {member_or_id.id} not found in guild")
                    return
        else:
            print(f"[RoleManager] Invalid member_or_id type: {type(member_or_id)}")
            return

        # Determine which tier role to add (if any)
        new_tier_role_id = 0
        if new_rank_str and new_rank_str.lower() != "unranked":
            parsed = parse_rank(new_rank_str)
            if parsed:
                new_tier_role_id = _get_tier_role_id(parsed[0])
                print(f"[RoleManager] {member.display_name}: rank='{new_rank_str}' → tier='{parsed[0]}' → role_id={new_tier_role_id}")
            else:
                print(f"[RoleManager] Could not parse rank string: '{new_rank_str}'")
        else:
            print(f"[RoleManager] {member.display_name}: stripping all tier roles (rank='{new_rank_str}')")

        # Collect all tier role IDs
        all_tier_role_ids = _get_all_tier_role_ids()
        if not all_tier_role_ids:
            print("[RoleManager] No tier role IDs configured! Check env vars.")
            return

        # Remove old tier roles
        roles_to_remove = [
            role for role in member.roles
            if role.id in all_tier_role_ids and role.id != new_tier_role_id
        ]
        if roles_to_remove:
            print(f"[RoleManager] Removing roles: {[r.name for r in roles_to_remove]}")
            await member.remove_roles(*roles_to_remove, reason="Tier role update")

        # Add new tier role (if needed and not already assigned)
        if new_tier_role_id:
            new_role = guild.get_role(new_tier_role_id)
            if not new_role:
                print(f"[RoleManager] Role ID {new_tier_role_id} not found in guild!")
                return
            if new_role not in member.roles:
                print(f"[RoleManager] Adding role '{new_role.name}' to {member.display_name}")
                await member.add_roles(new_role, reason="Tier role update")
            else:
                print(f"[RoleManager] {member.display_name} already has role '{new_role.name}'")

    except discord.Forbidden:
        print(f"[RoleManager] Missing permissions to update roles for {member_or_id}")
    except Exception as e:
        print(f"[RoleManager] Error updating tier role for {member_or_id}: {e}")
