from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryScope:
    """Identity scope attached to every memory lookup and write."""

    guild_id: int | None
    channel_id: int | None
    user_id: int | None

    @classmethod
    def from_message(cls, message):
        guild = getattr(message, "guild", None)
        author = getattr(message, "author", None)
        return cls(
            guild_id=getattr(guild, "id", None),
            channel_id=getattr(getattr(message, "channel", None), "id", None),
            user_id=getattr(author, "id", None),
        )

    def matches_record(self, record):
        if self.guild_id is None or record.get("guild_id") != self.guild_id:
            return False
            
        record_type = record.get("record_type")
        if record_type in ("server_lore", "community_term", "inside_joke", "nickname", "relationship", "event"):
            return True
            
        record_channel = record.get("channel_id")
        return record_channel in (None, self.channel_id)
