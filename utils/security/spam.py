"""Spam, raid and lockdown rules that don't need a Discord connection."""
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord

INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+", re.IGNORECASE)
EVERYONE_RE = re.compile(r"@(?:everyone|here)\b")

FLOOD_COUNT, FLOOD_SECONDS = 10, 7
MENTION_LIMIT = 8
INVITE_COUNT, INVITE_SECONDS = 5, 60
DUPLICATE_CHANNELS, DUPLICATE_SECONDS = 4, 30
TRACK_SECONDS = 60

STRIKE_RESET_SECONDS = 30 * 60
# strike number -> timeout length; strike 1 is a warning only
STRIKE_TIMEOUTS = {1: None, 2: timedelta(minutes=5), 3: timedelta(minutes=30)}

ACCOUNT_AGE_GATE = timedelta(days=3)
SPIKE_JOINS, SPIKE_SECONDS = 10, 30
SPIKE_KICK_AGE = timedelta(days=30)
LOCKDOWN_QUIET_SECONDS = 15 * 60

LOCK_PERMISSIONS = discord.Permissions(
    send_messages=True,
    send_messages_in_threads=True,
    create_public_threads=True,
    create_private_threads=True,
    add_reactions=True,
).value


class SpamDetector:
    """Tracks recent messages per user and decides when a spam rule fires."""

    def __init__(self):
        self._messages = defaultdict(deque)  # user_id -> deque[(ts, channel_id, message_id, normalized)]
        self._invites = defaultdict(deque)  # user_id -> deque[ts]

    def check(self, user_id, channel_id, message_id, content, mention_count, now=None):
        """Return (rule, messages_to_delete). rule is None when the message is fine."""
        now = time.monotonic() if now is None else now
        normalized = " ".join((content or "").lower().split())

        history = self._messages[user_id]
        history.append((now, channel_id, message_id, normalized))
        while history and now - history[0][0] > TRACK_SECONDS:
            history.popleft()

        invites = self._invites[user_id]
        for _ in INVITE_RE.findall(content or ""):
            invites.append(now)
        while invites and now - invites[0] > INVITE_SECONDS:
            invites.popleft()

        rule = None
        if mention_count >= MENTION_LIMIT:
            rule = "mass_mentions"
        elif sum(1 for ts, *_ in history if now - ts <= FLOOD_SECONDS) >= FLOOD_COUNT:
            rule = "message_flood"
        elif len(invites) >= INVITE_COUNT:
            rule = "invite_spam"
        elif normalized and len({
            ch for ts, ch, _, text in history if text == normalized and now - ts <= DUPLICATE_SECONDS
        }) >= DUPLICATE_CHANNELS:
            rule = "cross_channel_duplicate"

        if rule is None:
            return None, []

        to_delete = [(ch, mid) for ts, ch, mid, _ in history]
        self._messages.pop(user_id, None)
        self._invites.pop(user_id, None)
        return rule, to_delete


class StrikeLadder:
    def __init__(self):
        self._strikes = {}  # user_id -> (count, last_ts)

    def add(self, user_id, now=None):
        """Record an offence and return (strike_number, timeout_or_None)."""
        now = time.monotonic() if now is None else now
        count, last = self._strikes.get(user_id, (0, now))
        if now - last > STRIKE_RESET_SECONDS:
            count = 0
        count += 1
        self._strikes[user_id] = (count, now)
        return count, STRIKE_TIMEOUTS[min(count, 3)]


class JoinSpikeDetector:
    def __init__(self, joins=SPIKE_JOINS, seconds=SPIKE_SECONDS):
        self.joins = joins
        self.seconds = seconds
        self._joins = deque()  # (ts, member_id)

    def record(self, member_id, now=None) -> bool:
        now = time.monotonic() if now is None else now
        self._joins.append((now, member_id))
        while self._joins and now - self._joins[0][0] > self.seconds:
            self._joins.popleft()
        return len(self._joins) >= self.joins

    def recent_ids(self) -> list[int]:
        return [member_id for _, member_id in self._joins]


def account_age(created_at, now=None) -> timedelta:
    now = now or datetime.now(timezone.utc)
    return now - created_at


def mentions_everyone(content) -> bool:
    return bool(EVERYONE_RE.search(content or ""))


def locked_pair(allow: int, deny: int) -> tuple[int, int]:
    """Overwrite pair that keeps everything as-is except denying sending."""
    return allow & ~LOCK_PERMISSIONS, deny | LOCK_PERMISSIONS
