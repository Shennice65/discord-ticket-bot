from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    tool: str
    requester_id: int
    created_at: datetime


class ApprovalManager:
    """Approval boundary for future mutating tools.

    Read-only tools never enter this manager. Mutating tools can opt in later
    without changing the agent protocol or tool execution pipeline.
    """

    def __init__(self, required_tools=()):
        self.required_tools = set(required_tools)
        self.pending = {}

    def requires_approval(self, tool_name):
        return tool_name in self.required_tools

    def create(self, request_id, tool_name, requester_id):
        request = ApprovalRequest(
            request_id=request_id,
            tool=tool_name,
            requester_id=requester_id,
            created_at=datetime.now(timezone.utc),
        )
        self.pending[request_id] = request
        return request

    def resolve(self, request_id, approved):
        request = self.pending.pop(request_id, None)
        return bool(request and approved)
