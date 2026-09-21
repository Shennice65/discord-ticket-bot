import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

import aiohttp

from config import Config
from ai.llm import GenerationResult, llm

logger = logging.getLogger(__name__)


class AgentSidecarBridge:
    """WebSocket bridge to the Node agent runtime, with safe local fallback."""

    def __init__(self, bot):
        self.bot = bot
        self.host = getattr(Config, "AGENT_SIDECAR_HOST", "127.0.0.1")
        self.port = int(getattr(Config, "AGENT_SIDECAR_PORT", 8765))
        self.enabled = bool(getattr(Config, "AGENT_SIDECAR_ENABLED", True))
        self.process = None
        self._start_lock = asyncio.Lock()

    @property
    def url(self):
        return f"ws://{self.host}:{self.port}"

    async def start(self):
        if not self.enabled:
            return False
        async with self._start_lock:
            if self.process and self.process.returncode is None:
                return True
            sidecar_root = Path(__file__).resolve().parent.parent / "agent-sidecar"
            entrypoint = sidecar_root / "src" / "server.js"
            if not entrypoint.exists():
                return False
            try:
                self.process = await asyncio.create_subprocess_exec(
                    "node", str(entrypoint), "--port", str(self.port),
                    cwd=str(sidecar_root),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=os.environ.copy(),
                )
                await asyncio.sleep(0.25)
                return self.process.returncode is None
            except (OSError, asyncio.TimeoutError) as error:
                logger.warning("Agent sidecar unavailable error=%s", type(error).__name__)
                self.process = None
                return False

    async def stop(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
        self.process = None

    async def run(self, messages, tools, execute_tool, on_delta=None):
        if getattr(Config, "AI_PROVIDER", "gemini") != "openrouter":
            return None
        if not await self.start():
            return None
        request_id = uuid.uuid4().hex
        payload = {
            "type": "agent_request",
            "request_id": request_id,
            "messages": messages,
            "tools": tools,
            "max_steps": int(getattr(Config, "AGENT_MAX_STEPS", 4)),
            "model": llm.model,
            "fallback_models": llm.fallback_models,
            "api_key": llm.api_key,
        }
        timeout = aiohttp.ClientTimeout(total=90)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(self.url, heartbeat=20) as socket:
                    await socket.send_json(payload)
                    async for event in socket:
                        if event.type == aiohttp.WSMsgType.ERROR:
                            raise RuntimeError("agent sidecar websocket failed")
                        if event.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = json.loads(event.data)
                        if data.get("request_id") != request_id:
                            continue
                        event_type = data.get("type")
                        if event_type == "delta" and on_delta:
                            await on_delta(str(data.get("text") or ""))
                        elif event_type == "tool_request":
                            result = await execute_tool(
                                str(data.get("name") or ""),
                                data.get("arguments") or {},
                            )
                            await socket.send_json({
                                "type": "tool_result",
                                "request_id": request_id,
                                "call_id": data.get("call_id"),
                                "result": result,
                            })
                        elif event_type == "final":
                            return GenerationResult(
                                text=str(data.get("text") or ""),
                                model=str(data.get("model") or llm.model),
                                usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
                            )
                        elif event_type == "error":
                            raise RuntimeError(str(data.get("error") or "sidecar failed"))
        except Exception as error:
            logger.warning("Agent sidecar request failed error=%s", type(error).__name__)
            return None
        return None
