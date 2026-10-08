"""Sliding-window counters and action classification for anti-nuke detection."""
import time
from collections import defaultdict, deque

import discord

WINDOW_SECONDS = 15

# bucket -> number of actions by one user inside WINDOW_SECONDS that triggers an incident
THRESHOLDS = {
    "channel_delete": 3,
    "role_delete": 3,
    "ban_kick": 3,
    "channel_create": 5,
    "role_create": 5,
    "webhook_create": 2,
    "mass_edit": 8,
}

ACTION_BUCKETS = {
    "channel_delete": "channel_delete",
    "role_delete": "role_delete",
    "ban": "ban_kick",
    "kick": "ban_kick",
    "channel_create": "channel_create",
    "role_create": "role_create",
    "webhook_create": "webhook_create",
    "channel_update": "mass_edit",
    "overwrite_create": "mass_edit",
    "overwrite_update": "mass_edit",
    "overwrite_delete": "mass_edit",
    "role_update": "mass_edit",
    "member_role_update": "mass_edit",
    "webhook_update": "mass_edit",
    "webhook_delete": "mass_edit",
    "guild_update": "mass_edit",
    "emoji_delete": "mass_edit",
    "sticker_delete": "mass_edit",
}

DANGEROUS_PERMISSIONS = discord.Permissions(
    administrator=True,
    manage_guild=True,
    manage_roles=True,
    manage_channels=True,
    manage_webhooks=True,
    ban_members=True,
    kick_members=True,
).value


def grants_dangerous(before_value, after_value) -> bool:
    """True when after_value adds a dangerous permission bit that before_value lacked."""
    if after_value is None:
        return False
    added = int(after_value) & ~int(before_value or 0)
    return bool(added & DANGEROUS_PERMISSIONS)


def classify(
    action: str,
    *,
    before_perms=None,
    after_perms=None,
    target_is_everyone: bool = False,
    added_role_perms=(),
    vanity_changed: bool = False,
):
    """Return (bucket, instant) for an audit log action name.

    instant=True means a single occurrence is enough to start an incident.
    """
    bucket = ACTION_BUCKETS.get(action)

    if action in ("bot_add", "member_prune"):
        return bucket, True
    if action in ("role_update", "role_create") and grants_dangerous(before_perms, after_perms):
        return bucket, True
    if action in ("overwrite_create", "overwrite_update") and target_is_everyone and grants_dangerous(before_perms, after_perms):
        return bucket, True
    if action == "member_role_update" and any(grants_dangerous(0, value) for value in added_role_perms):
        return bucket, True
    if action == "guild_update" and vanity_changed:
        return bucket, True
    return bucket, False


class ActionTracker:
    """Counts actions per (actor, bucket) inside a rolling window."""

    def __init__(self, thresholds=None, window: float = WINDOW_SECONDS):
        self.thresholds = thresholds or THRESHOLDS
        self.window = window
        self._events = defaultdict(deque)

    def record(self, actor_id: int, bucket: str, now: float | None = None) -> bool:
        if bucket not in self.thresholds:
            return False
        now = time.monotonic() if now is None else now
        events = self._events[(actor_id, bucket)]
        events.append(now)
        while events and now - events[0] > self.window:
            events.popleft()
        return len(events) >= self.thresholds[bucket]

    def reset(self, actor_id: int):
        for key in [key for key in self._events if key[0] == actor_id]:
            del self._events[key]
