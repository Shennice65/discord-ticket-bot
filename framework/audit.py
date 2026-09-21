import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def record_agent_event(bot, *, event, tool=None, model=None, usage=None, status="ok"):
    """Record metadata only; message contents and secrets are intentionally excluded."""
    document = {
        "event": event,
        "tool": tool,
        "model": model,
        "usage": usage if isinstance(usage, dict) else {},
        "status": status,
        "created_at": datetime.now(timezone.utc),
    }
    logger.info(
        "AI audit event=%s tool=%s model=%s status=%s",
        event, tool, model, status,
    )
    collection = getattr(getattr(bot, "db", None), "db", None)
    collection = getattr(collection, "ai_audit", None)
    if collection is not None:
        try:
            await collection.insert_one(document)
        except Exception as error:
            logger.debug("AI audit persistence unavailable error=%s", type(error).__name__)
