from datetime import datetime, timezone

BACKUP_MESSAGE_LIMIT = 500
SNAPSHOT_KEEP = 5


class SecurityMixin:
    async def get_security_whitelist(self) -> set[int]:
        cached = getattr(self, "_security_whitelist_cache", None)
        if cached is None:
            cached = set(await self.get_setting("security_whitelist", []))
            self._security_whitelist_cache = cached
        return cached

    async def set_security_whitelist(self, user_ids):
        self._security_whitelist_cache = set(user_ids)
        await self.set_setting("security_whitelist", sorted(user_ids))

    async def get_backup_channels(self) -> set[int]:
        cached = getattr(self, "_security_backup_channels_cache", None)
        if cached is None:
            cached = set(await self.get_setting("security_backup_channels", []))
            self._security_backup_channels_cache = cached
        return cached

    async def set_backup_channels(self, channel_ids):
        self._security_backup_channels_cache = set(channel_ids)
        await self.set_setting("security_backup_channels", sorted(channel_ids))

    # Incidents
    async def create_security_incident(self, doc: dict) -> int:
        incident_id = await self._next_id("security_incidents")
        doc = {**doc, "id": incident_id, "created_at": datetime.now(timezone.utc)}
        await self.security_incidents.insert_one(doc)
        return incident_id

    async def update_security_incident(self, incident_id: int, fields: dict):
        await self.security_incidents.update_one({"id": incident_id}, {"$set": fields})

    async def get_security_incident(self, incident_id: int):
        return await self.security_incidents.find_one({"id": incident_id})

    async def list_security_incidents(self, limit: int = 10):
        cursor = self.security_incidents.find({}, {"actions": 0}).sort("id", -1).limit(limit)
        return await cursor.to_list(length=limit)

    # Snapshots
    async def save_security_snapshot(self, doc: dict) -> int:
        snapshot_id = await self._next_id("security_snapshots")
        await self.security_snapshots.insert_one({**doc, "id": snapshot_id, "created_at": datetime.now(timezone.utc)})
        stale = await self.security_snapshots.find({}, {"id": 1}).sort("id", -1).skip(SNAPSHOT_KEEP).to_list(length=None)
        if stale:
            await self.security_snapshots.delete_many({"id": {"$in": [s["id"] for s in stale]}})
        return snapshot_id

    async def get_security_snapshot(self, snapshot_id: int | None = None):
        if snapshot_id is None:
            return await self.security_snapshots.find_one({}, sort=[("id", -1)])
        return await self.security_snapshots.find_one({"id": snapshot_id})

    async def list_security_snapshots(self):
        cursor = self.security_snapshots.find({}, {"id": 1, "created_at": 1}).sort("id", -1)
        return await cursor.to_list(length=SNAPSHOT_KEEP)

    # Message backup
    async def backup_message(self, doc: dict):
        await self.security_msg_backup.update_one(
            {"message_id": doc["message_id"]}, {"$set": doc}, upsert=True,
        )

    async def trim_message_backup(self, channel_id: int):
        oldest_kept = await self.security_msg_backup.find(
            {"channel_id": channel_id}, {"created_at": 1},
        ).sort("created_at", -1).skip(BACKUP_MESSAGE_LIMIT - 1).limit(1).to_list(length=1)
        if oldest_kept:
            await self.security_msg_backup.delete_many(
                {"channel_id": channel_id, "created_at": {"$lt": oldest_kept[0]["created_at"]}},
            )

    async def edit_backed_up_message(self, message_id: int, content: str):
        await self.security_msg_backup.update_one({"message_id": message_id}, {"$set": {"content": content}})

    async def mark_backed_up_message_deleted(self, message_ids):
        await self.security_msg_backup.update_many(
            {"message_id": {"$in": list(message_ids)}, "deleted_at": None},
            {"$set": {"deleted_at": datetime.now(timezone.utc)}},
        )

    async def get_backed_up_messages(self, channel_id: int, deleted_after: datetime):
        """Messages to replay: not deleted, or deleted during the incident (e.g. a purge before the delete)."""
        cursor = self.security_msg_backup.find({
            "channel_id": channel_id,
            "$or": [{"deleted_at": None}, {"deleted_at": {"$gte": deleted_after}}],
        }).sort("created_at", 1)
        return await cursor.to_list(length=BACKUP_MESSAGE_LIMIT)

    async def move_message_backup(self, old_channel_id: int, new_channel_id: int):
        await self.security_msg_backup.update_many({"channel_id": old_channel_id}, {"$set": {"channel_id": new_channel_id}})

    async def delete_message_backup(self, channel_id: int):
        await self.security_msg_backup.delete_many({"channel_id": channel_id})

    # Lockdown
    async def get_lockdown(self, guild_id: int):
        return await self.security_lockdown.find_one({"_id": guild_id})

    async def save_lockdown(self, guild_id: int, doc: dict):
        await self.security_lockdown.replace_one({"_id": guild_id}, {**doc, "_id": guild_id}, upsert=True)

    async def clear_lockdown(self, guild_id: int):
        await self.security_lockdown.delete_one({"_id": guild_id})
