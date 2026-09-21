import asyncio
import json
import logging
import re
import time
from types import SimpleNamespace

import aiohttp

from config import Config

logger = logging.getLogger(__name__)


def sanitize_model_text(text):
    """Remove leaked reasoning markers before model text reaches Discord."""
    value = str(text or "")
    value = re.sub(r"<(?:(?:thought|thinking|analysis))>.*?</(?:(?:thought|thinking|analysis))>", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"</?(?:thought|thinking|analysis)>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:p|div|span|section|article)(?:\s[^>]*)?>", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\bBOT_RESPONSE\s+to_user_id=\d+\s+guild_id=\d+\s+channel_id=\d+\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\bDISCORD_USER\s+id=\d+\s+name=[^\n]+?\s+guild_id=\d+\s+channel_id=\d+\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\n[ \t]*(?:\n[ \t]*)+", "\n", value)
    return value.strip()


class ToolCall:
    def __init__(self, call_id, name, arguments):
        self.call_id = str(call_id or "")
        self.name = str(name or "")
        self.arguments = arguments if isinstance(arguments, dict) else {}

    def __eq__(self, other):
        return (
            isinstance(other, ToolCall)
            and self.call_id == other.call_id
            and self.name == other.name
            and self.arguments == other.arguments
        )

    def __repr__(self):
        return f"ToolCall({self.call_id!r}, {self.name!r}, {self.arguments!r})"


class GenerationResult:
    def __init__(self, text, tool_calls=(), model="", usage=None, assistant_message=None):
        self.text = sanitize_model_text(text)
        self.tool_calls = tuple(tool_calls or ())
        self.model = model
        self.usage = usage if isinstance(usage, dict) else None
        self.assistant_message = assistant_message


class GeminiLLM:
    """Provider-neutral Gemini REST adapter used by the agent and memory paths."""

    API_URL = "https://generativelanguage.googleapis.com/v1beta"
    ATTEMPT_TIMEOUT = 45

    def __init__(self):
        self.api_keys = self._clean_keys(getattr(Config, "GEMINI_API_KEYS", ()))
        self.current_client_index = 0
        self.model = getattr(Config, "GEMINI_MODEL", "gemini-3.8-flash")
        self.fallback_models = list(getattr(Config, "GEMINI_FALLBACK_MODELS", ()))
        self.embedding_model = getattr(Config, "GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
        self.embedding_dimensions = int(getattr(Config, "GEMINI_EMBEDDING_DIMENSIONS", 256))
        self._db_config_checked_at = 0.0

    @staticmethod
    def _clean_keys(raw_keys):
        if isinstance(raw_keys, str):
            raw_keys = raw_keys.split(",")
        return list(dict.fromkeys(
            str(value).strip().strip("'\"")
            for value in (raw_keys or ())
            if str(value).strip() and str(value).strip() != "your_gemini_api_key_here"
        ))

    @property
    def api_key(self):
        if not self.api_keys:
            return ""
        return self.api_keys[self.current_client_index % len(self.api_keys)]

    @property
    def client(self):
        return self if self.api_key else None

    def rotate_key(self):
        if self.api_keys:
            self.current_client_index = (self.current_client_index + 1) % len(self.api_keys)

    async def ensure_keys(self, db):
        now = time.monotonic()
        if now - self._db_config_checked_at < 300:
            return bool(self.api_keys)
        self._db_config_checked_at = now
        env_keys = self._clean_keys(getattr(Config, "GEMINI_API_KEYS", ()))
        db_keys = []
        if db is not None and getattr(db, "db", None) is not None:
            try:
                config_doc = await db.db.config.find_one({"_id": "api_keys"})
                db_keys = self._clean_keys((config_doc or {}).get("GEMINI_API_KEY", ()))
            except Exception as error:
                logger.warning("Gemini configuration lookup failed error=%s", type(error).__name__)
        keys = list(dict.fromkeys([*db_keys, *env_keys]))
        if keys != self.api_keys:
            self.api_keys = keys
            self.current_client_index = 0
        return bool(self.api_keys)

    @staticmethod
    def _text_parts(content):
        if isinstance(content, str):
            return [{"text": content}]
        parts = []
        for item in content or ():
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append({"text": str(item.get("text") or "")})
            elif item.get("type") == "image_url":
                image = item.get("image_url") or {}
                url = image.get("url") if isinstance(image, dict) else image
                if url:
                    parts.append({"text": f"[image attachment: {url}]"})
        return parts or [{"text": ""}]

    @classmethod
    def _convert_messages(cls, messages):
        system_text = []
        contents = []
        call_names = {}
        for message in messages or ():
            role = message.get("role")
            if role == "system":
                system_text.extend(part.get("text", "") for part in cls._text_parts(message.get("content")))
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                name = call_names.get(call_id, call_id or "tool_result")
                raw = message.get("content")
                try:
                    result = json.loads(raw) if isinstance(raw, str) else raw
                except (TypeError, ValueError):
                    result = raw
                contents.append({"role": "user", "parts": [{"functionResponse": {
                    "id": call_id, "name": name, "response": {"result": result},
                }}]})
                continue
            if role == "assistant" and message.get("_gemini_content"):
                raw_parts = message["_gemini_content"].get("parts") or []
                for part in raw_parts:
                    function_call = part.get("functionCall") or {}
                    call_id = str(function_call.get("id") or function_call.get("name") or "tool_call")
                    if function_call.get("name"):
                        call_names[call_id] = function_call["name"]
                contents.append({"role": "model", "parts": raw_parts})
                continue
            gemini_role = "model" if role == "assistant" else "user"
            tool_calls = message.get("tool_calls") or ()
            parts = [] if tool_calls and message.get("content") is None else cls._text_parts(message.get("content"))
            for call in tool_calls:
                function = call.get("function") or {}
                call_id = str(call.get("id") or function.get("name") or "tool_call")
                name = str(function.get("name") or "")
                call_names[call_id] = name
                raw_args = function.get("arguments") or {}
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (TypeError, ValueError):
                    arguments = {}
                parts.append({"functionCall": {"name": name, "args": arguments}})
            contents.append({"role": gemini_role, "parts": parts})
        return "\n".join(text for text in system_text if text), contents

    @staticmethod
    def _convert_schema(schema):
        if isinstance(schema, dict):
            converted = {}
            for key, value in schema.items():
                if key == "additionalProperties":
                    continue
                if key == "type" and isinstance(value, str):
                    converted[key] = value.upper()
                else:
                    converted[key] = GeminiLLM._convert_schema(value)
            return converted
        if isinstance(schema, list):
            return [GeminiLLM._convert_schema(item) for item in schema]
        return schema

    @staticmethod
    def _convert_tools(tools):
        declarations = []
        for item in tools or ():
            function = item.get("function") if isinstance(item, dict) else None
            if not function:
                continue
            declarations.append({
                "name": function.get("name"),
                "description": function.get("description", ""),
                "parameters": GeminiLLM._convert_schema(function.get("parameters") or {
                    "type": "object", "properties": {}, "additionalProperties": False,
                }),
            })
        return [{"functionDeclarations": declarations}] if declarations else []

    async def _request(self, model, endpoint, payload, api_key=None):
        key = api_key or self.api_key
        if not key:
            raise RuntimeError("No Gemini API key configured.")
        timeout = aiohttp.ClientTimeout(total=self.ATTEMPT_TIMEOUT)
        url = f"{self.API_URL}/models/{model}:{endpoint}"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, params={"key": key}, headers={"Content-Type": "application/json"}, json=payload
            ) as response:
                body = await response.text()
                if response.status >= 400:
                    detail = body
                    try:
                        detail = (json.loads(body).get("error") or {}).get("message") or body
                    except (TypeError, ValueError):
                        pass
                    raise RuntimeError(f"Gemini {response.status}: {str(detail)[:300]}")
                try:
                    return json.loads(body)
                except ValueError as error:
                    raise RuntimeError("Gemini returned invalid JSON.") from error

    async def _request_with_fallback(self, endpoint, models, payload):
        last_error = None
        for model in models:
            for _attempt in range(max(1, len(self.api_keys))):
                try:
                    return model, await self._request(model, endpoint, payload)
                except Exception as error:
                    last_error = error
                    if len(self.api_keys) > 1:
                        self.rotate_key()
                    await asyncio.sleep(0.15)
            logger.warning("Gemini model exhausted model=%s error=%s", model, type(last_error).__name__)
        raise last_error or RuntimeError("All Gemini models exhausted.")

    async def generate(self, messages, tools=None, temperature=0.82, max_tokens=1200):
        system_text, contents = self._convert_messages(messages)
        payload = {"contents": contents, "generationConfig": {
            "temperature": temperature, "maxOutputTokens": max_tokens,
        }}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        converted_tools = self._convert_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
        models = list(dict.fromkeys([self.model, *self.fallback_models]))
        model, response = await self._request_with_fallback("generateContent", models, payload)
        candidate = (response.get("candidates") or [{}])[0]
        response_parts = ((candidate.get("content") or {}).get("parts") or [])
        text = "".join(str(part.get("text") or "") for part in response_parts if part.get("text"))
        calls = []
        for index, part in enumerate(response_parts):
            function_call = part.get("functionCall")
            if function_call and function_call.get("name"):
                calls.append(ToolCall(
                    function_call.get("id") or f"gemini-call-{index}",
                    function_call["name"], function_call.get("args") or {},
                ))
        assistant_message = {"role": "assistant", "content": text or None, "tool_calls": [{
            "id": call.call_id, "type": "function", "function": {
                "name": call.name, "arguments": json.dumps(call.arguments),
            }
        } for call in calls], "_gemini_content": candidate.get("content") or {"parts": []}}
        metadata = response.get("usageMetadata") or {}
        usage = {
            "prompt_tokens": metadata.get("promptTokenCount", 0),
            "completion_tokens": metadata.get("candidatesTokenCount", 0),
            "total_tokens": metadata.get("totalTokenCount", 0),
        }
        return GenerationResult(text, tuple(calls), model, usage, assistant_message)

    def _normalize_embedding(self, vector):
        values = list(vector or [])[:self.embedding_dimensions]
        values.extend([0.0] * (self.embedding_dimensions - len(values)))
        return values

    async def embed(self, texts):
        values = [texts] if isinstance(texts, str) else list(texts or [])
        result = []
        for value in values:
            _model, response = await self._request_with_fallback(
                "embedContent", [self.embedding_model], {
                    "content": {"parts": [{"text": str(value)}]},
                    "outputDimensionality": self.embedding_dimensions,
                }
            )
            result.append(self._normalize_embedding((response.get("embedding") or {}).get("values")))
        return result

    async def generate_content(self, model=None, contents=None, config=None):
        # Compatibility for older extensions/tests that supplied SDK clients
        # directly. The active path uses the REST adapter above.
        legacy_clients = getattr(self, "clients", None)
        if legacy_clients and not getattr(self, "api_keys", None):
            for attempt in range(len(legacy_clients)):
                client = legacy_clients[self.current_client_index]
                try:
                    return await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=model, contents=contents, config=config
                        ),
                        timeout=self.ATTEMPT_TIMEOUT,
                    )
                except Exception:
                    if attempt < len(legacy_clients) - 1:
                        self.current_client_index = (self.current_client_index + 1) % len(legacy_clients)
            raise RuntimeError("All legacy Gemini clients failed.")
        result = await self.generate([{"role": "user", "content": str(contents or "")}])
        function_calls = tuple(SimpleNamespace(name=call.name, args=call.arguments) for call in result.tool_calls)
        return SimpleNamespace(text=result.text, function_calls=function_calls)

    async def embed_content(self, model=None, contents=None, config=None):
        values = contents if isinstance(contents, list) else [contents]
        embeddings = await self.embed(values)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=value) for value in embeddings])


OpenRouterLLM = GeminiLLM
llm = GeminiLLM()
