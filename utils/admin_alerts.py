from __future__ import annotations

from datetime import datetime, timezone

import discord
from config import Config


async def send_master_admin_dm(bot, *, content: str | None = None, embed: discord.Embed | None = None) -> bool:
    """DM the configured master admin, falling back to an API lookup."""
    try:
        admin = bot.get_user(Config.MASTER_ADMIN_ID)
        if admin is None:
            admin = await bot.fetch_user(Config.MASTER_ADMIN_ID)
        await admin.send(content=content, embed=embed)
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False


async def check_and_alert_alt_risk(bot, member: discord.Member) -> None:
    """Evaluate conservative alt signals and send one deduplicated private alert."""
    try:
        now = datetime.now(timezone.utc)
        signals: list[tuple[str, int]] = []

        account_days = max(0, (now - member.created_at).days)
        if account_days < 7:
            signals.append((f"Discord account is {account_days} day(s) old", 30))

        joined_at = member.joined_at
        if joined_at:
            if joined_at.tzinfo is None:
                joined_at = joined_at.replace(tzinfo=timezone.utc)
            joined_days = max(0, (now - joined_at).days)
            if joined_days < 7:
                signals.append((f"Joined the ATL guild {joined_days} day(s) ago", 10))

        database = bot.db.db
        roblox_identity = await database.roblox_usernames.find_one({"_id": member.id})
        linked_accounts: list[int] = []
        if roblox_identity and roblox_identity.get("roblox_id"):
            cursor = database.roblox_usernames.find({
                "roblox_id": roblox_identity["roblox_id"],
                "_id": {"$ne": member.id},
            })
            linked_accounts = [int(row["_id"]) async for row in cursor]
            if linked_accounts:
                signals.append((f"Roblox account is also linked to {len(linked_accounts)} Discord account(s)", 60))

        score = sum(weight for _, weight in signals)
        if score < 30:
            return

        fingerprint = f"{member.id}:{'|'.join(sorted(text for text, _ in signals))}"
        alerts = database.alt_risk_alerts
        if await alerts.find_one({"_id": fingerprint}):
            return
        await alerts.insert_one({
            "_id": fingerprint,
            "user_id": member.id,
            "score": score,
            "signals": [text for text, _ in signals],
            "created_at": now,
            "status": "open",
        })

        severity = "Strong association" if score >= 100 else "Review" if score >= 60 else "Watch"
        embed = discord.Embed(
            title="Possible Alt Account",
            description=f"{member.mention} (`{member.id}`) was flagged for private review.",
            color=discord.Color.orange(),
            timestamp=now,
        )
        embed.add_field(name="Risk", value=f"**{score}** — {severity}", inline=False)
        embed.add_field(
            name="Signals",
            value="\n".join(f"• {text} (+{weight})" for text, weight in signals),
            inline=False,
        )
        if linked_accounts:
            embed.add_field(
                name="Other linked Discord accounts",
                value="\n".join(f"<@{user_id}> (`{user_id}`)" for user_id in linked_accounts[:10]),
                inline=False,
            )
        embed.set_footer(text="Review manually — risk signals are not proof of alting.")
        await send_master_admin_dm(bot, embed=embed)
    except Exception as error:
        print(f"[ALT RISK] Failed to evaluate {member.id}: {error}", flush=True)
