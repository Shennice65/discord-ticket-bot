import json
import logging
from dataclasses import dataclass

from framework.approvals import ApprovalManager
from framework.audit import record_agent_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolEnvelope:
    status: str
    tool: str
    version: int
    data: object = None
    error: str | None = None
    meta: dict | None = None

    def as_dict(self):
        return {
            "status": self.status,
            "tool": self.tool,
            "version": self.version,
            "data": self.data,
            "error": self.error,
            "meta": self.meta or {},
        }


def _bounded(value, *, depth=0, max_depth=4, max_items=40, max_text=1500):
    if depth >= max_depth:
        return "[nested value omitted]"
    if isinstance(value, str):
        return value[:max_text]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _bounded(item, depth=depth + 1, max_depth=max_depth,
                                      max_items=max_items, max_text=max_text)
            for key, item in list(value.items())[:max_items]
        }
    if isinstance(value, (list, tuple)):
        return [
            _bounded(item, depth=depth + 1, max_depth=max_depth,
                     max_items=max_items, max_text=max_text)
            for item in list(value)[:max_items]
        ]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)[:max_text]


class ToolExecutionPipeline:
    """Deterministic envelope, validation, compression, and audit boundary."""

    VERSION = 1

    def __init__(self, registry, bot, approvals=None, external_tools=None):
        self.registry = registry
        self.bot = bot
        self.approvals = approvals or ApprovalManager()
        self.external_tools = dict(external_tools or {})

    def register_external_tool(self, definition, handler):
        """Register a request-scoped adapter without exposing database access."""
        name = definition["function"]["name"]
        self.external_tools[name] = (definition, handler)

    async def execute(self, name, arguments, message, context):
        known_tools = {
            item["function"]["name"] for item in self.registry.definitions
        }
        known_tools.update(self.external_tools)
        if name not in known_tools:
            envelope = ToolEnvelope("error", name, self.VERSION, error="tool is not enabled")
            return envelope.as_dict()
        if self.approvals.requires_approval(name):
            envelope = ToolEnvelope("error", name, self.VERSION, error="approval_required")
            await record_agent_event(self.bot, event="tool_rejected", tool=name, status="approval_required")
            return envelope.as_dict()

        try:
            if name in self.external_tools:
                raw = await self.external_tools[name][1](arguments or {}, message, context)
            else:
                raw = await self.registry.execute(name, arguments or {}, message, context)
            if isinstance(raw, dict) and raw.get("error"):
                envelope = ToolEnvelope("error", name, self.VERSION, error=str(raw["error"]))
            else:
                envelope = ToolEnvelope(
                    "ok", name, self.VERSION,
                    data=_bounded(raw),
                    meta={"compressed": True},
                )
            await record_agent_event(self.bot, event="tool_completed", tool=name, status=envelope.status)
            return envelope.as_dict()
        except Exception as error:
            logger.warning("Tool pipeline failed tool=%s error=%s", name, type(error).__name__)
            await record_agent_event(self.bot, event="tool_failed", tool=name, status="error")
            return ToolEnvelope(
                "error", name, self.VERSION,
                error="tool execution failed",
            ).as_dict()
