"""Serialize guild structure so deleted roles/channels can be recreated."""
import discord

CATEGORY_TYPE = 4

# Lower runs first. Roles must exist before channels so overwrites can point at them,
# and categories must exist before the channels placed inside them.
RESTORE_ORDER = {
    "role_delete": 0,
    "channel_delete": 1,
    "ban": 3,
    "role_create": 4,
    "channel_create": 4,
    "webhook_create": 4,
    "bot_add": 4,
    "role_update": 5,
    "overwrite_create": 5,
    "overwrite_update": 5,
    "overwrite_delete": 5,
    "channel_update": 5,
    "member_role_update": 6,
    "guild_update": 7,
}


def _overwrite_entries(channel):
    raw = getattr(channel, "_overwrites", None)
    if raw is not None:
        return [
            {"id": ow.id, "type": int(getattr(ow, "type", 0)), "allow": int(ow.allow), "deny": int(ow.deny)}
            for ow in raw
        ]
    entries = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        is_role = isinstance(target, discord.Role) or getattr(target, "type", None) is discord.Role
        entries.append({"id": target.id, "type": 0 if is_role else 1, "allow": allow.value, "deny": deny.value})
    return entries


def serialize_channel(channel) -> dict:
    channel_type = getattr(channel.type, "value", channel.type)
    data = {
        "id": channel.id,
        "type": int(channel_type),
        "name": channel.name,
        "position": channel.position,
        "category_id": getattr(channel, "category_id", None),
        "topic": getattr(channel, "topic", None),
        "nsfw": bool(getattr(channel, "nsfw", False)),
        "slowmode_delay": int(getattr(channel, "slowmode_delay", 0) or 0),
        "bitrate": getattr(channel, "bitrate", None),
        "user_limit": getattr(channel, "user_limit", None),
        "overwrites": _overwrite_entries(channel),
    }
    if data["type"] == CATEGORY_TYPE:
        data["children"] = [child.id for child in getattr(channel, "channels", [])]
    return data


def serialize_role(role, member_ids=None) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "color": role.colour.value,
        "hoist": role.hoist,
        "mentionable": role.mentionable,
        "permissions": role.permissions.value,
        "position": role.position,
        "unicode_emoji": getattr(role, "unicode_emoji", None),
        "members": sorted(member_ids or []),
    }


def remap_overwrites(overwrites, id_map) -> list[dict]:
    """Point overwrite targets at recreated role ids where a role was restored."""
    return [{**ow, "id": id_map.get(ow["id"], ow["id"])} for ow in overwrites]


def restore_sort_key(entry: dict):
    data = entry.get("data") or {}
    is_category = data.get("type") == CATEGORY_TYPE
    return (
        RESTORE_ORDER.get(entry["action"], 9),
        0 if is_category else 1,
        data.get("position", 0),
    )


def order_for_restore(entries) -> list[dict]:
    return sorted(entries, key=restore_sort_key)
