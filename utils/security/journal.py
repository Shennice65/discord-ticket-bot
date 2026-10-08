"""Short-lived per-actor record of audited actions, kept so they can be undone."""
import time
from collections import defaultdict, deque

JOURNAL_SECONDS = 60


class ActionJournal:
    def __init__(self, keep_seconds: float = JOURNAL_SECONDS):
        self.keep_seconds = keep_seconds
        self._entries = defaultdict(deque)

    def add(self, actor_id: int, entry: dict, now: float | None = None):
        now = time.monotonic() if now is None else now
        entries = self._entries[actor_id]
        entries.append((now, entry))
        while entries and now - entries[0][0] > self.keep_seconds:
            entries.popleft()

    def take(self, actor_id: int) -> list[dict]:
        """Remove and return every recorded entry for actor_id, oldest first."""
        entries = self._entries.pop(actor_id, deque())
        return [entry for _, entry in entries]
