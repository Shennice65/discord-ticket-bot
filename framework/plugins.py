import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Small plugin contract for future server-scoped tools and commands."""

    def __init__(self):
        self.tools = {}
        self.commands = {}
        self.background_tasks = {}

    def register_tool(self, name, handler, *, read_only=True, risk="low",
                      description=None, parameters=None):
        self.tools[name] = {
            "handler": handler,
            "read_only": read_only,
            "risk": risk,
            "definition": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description or f"Read-only plugin lookup: {name}.",
                    "parameters": parameters or {
                        "type": "object", "properties": {},
                        "required": [], "additionalProperties": False,
                    },
                },
            },
        }

    def register_command(self, name, handler):
        self.commands[name] = handler

    def register_background_task(self, name, handler):
        self.background_tasks[name] = handler

    def load_directory(self, directory="plugins"):
        root = Path(directory)
        if not root.exists():
            return 0
        loaded = 0
        for path in sorted(root.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module = importlib.import_module(f"{root.name}.{path.stem}")
            register = getattr(module, "register", None)
            if register:
                register(self)
                loaded += 1
        return loaded
