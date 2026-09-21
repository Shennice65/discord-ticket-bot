import asyncio
import logging
import time
from google import genai
from google.genai import types
from google.genai.errors import APIError

from config import Config

logger = logging.getLogger(__name__)

class GeminiLLM:
    ATTEMPT_TIMEOUT = 15
    MODEL_FALLBACKS = {
        "generate_content": ("gemini-3.5-flash", "gemini-3.5-flash-lite"),
        "embed_content": ("gemini-embedding-2",),
    }

    def __init__(self):
        self.api_keys = self._clean_keys(Config.GEMINI_API_KEYS)
        self.clients = self._build_clients(self.api_keys)
        logger.info("Loaded Gemini API configuration keys=%s", len(self.api_keys))
        self.current_client_index = 0
        self._db_config_checked_at = 0.0

    @staticmethod
    def _clean_keys(raw_keys):
        if isinstance(raw_keys, str):
            raw_keys = raw_keys.split(",")
        cleaned = []
        for value in raw_keys or ():
            key = str(value).strip().strip("'\"")
            if key and key != "your_gemini_api_key_here" and key not in cleaned:
                cleaned.append(key)
        return cleaned

    @staticmethod
    def _build_clients(api_keys):
        clients = []
        for key in api_keys:
            # AQ.* keys have historically needed the explicit API-key header
            # with older google-genai releases. Keep that compatibility path;
            # modern releases also support it directly.
            if not key.startswith("AIza"):
                clients.append(genai.Client(
                    api_key="dummy",
                    http_options=types.HttpOptions(headers={"x-goog-api-key": key}),
                ))
            else:
                clients.append(genai.Client(api_key=key))
        return clients

    @property
    def client(self):
        if not self.clients:
            return None
        return self.clients[self.current_client_index]

    def rotate_key(self):
        if not self.clients:
            return
        self.current_client_index = (self.current_client_index + 1) % len(self.clients)
        logger.warning(f"Rotated to Gemini API key index {self.current_client_index}.")

    async def ensure_keys(self, db):
        """Load Mongo-configured keys once per refresh window, with env fallback."""
        now = time.monotonic()
        if now - getattr(self, "_db_config_checked_at", 0.0) < 300:
            return bool(getattr(self, "clients", None))
        self._db_config_checked_at = now

        env_keys = self._clean_keys(getattr(Config, "GEMINI_API_KEYS", ()))
        db_keys = []
        if db is None or getattr(db, "db", None) is None:
            if not getattr(self, "clients", None) and env_keys:
                self.api_keys = env_keys
                self.clients = self._build_clients(env_keys)
            return bool(getattr(self, "clients", None))

        try:
            config_doc = await db.db.config.find_one({"_id": "api_keys"})
            if config_doc and config_doc.get("GEMINI_API_KEY"):
                db_keys = self._clean_keys(config_doc.get("GEMINI_API_KEY"))
            api_keys = list(dict.fromkeys([*db_keys, *env_keys]))
            if api_keys:
                if api_keys != getattr(self, "api_keys", ()):
                    self.api_keys = api_keys
                    self.clients = self._build_clients(api_keys)
                    self.current_client_index = 0
                return True
        except Exception as error:
            logger.error("Error fetching Gemini key configuration error=%s", type(error).__name__)

        if not getattr(self, "clients", None) and env_keys:
            self.api_keys = env_keys
            self.clients = self._build_clients(env_keys)
            self.current_client_index = 0
        return bool(getattr(self, "clients", None))

    async def generate_content(self, model: str, contents: list, config: types.GenerateContentConfig = None) -> types.GenerateContentResponse:
        """Call Gemini with bounded attempts, key rotation, and model fallback."""
        if not self.clients:
            raise RuntimeError("No Gemini API keys configured.")
        requested_models = list(dict.fromkeys((model, *self.MODEL_FALLBACKS.get("generate_content", ()))))
        for model_name in requested_models:
            for attempt in range(len(self.clients)):
                client = self.client
                try:
                    return await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=model_name, contents=contents, config=config
                        ),
                        timeout=self.ATTEMPT_TIMEOUT,
                    )
                except Exception as error:
                    retryable = isinstance(error, (asyncio.TimeoutError, TimeoutError))
                    if isinstance(error, APIError):
                        # Provider errors include quota, authorization, transient,
                        # and service failures; rotate before trying the fallback model.
                        retryable = True
                    if not retryable or attempt >= len(self.clients) - 1:
                        logger.warning("Gemini attempt failed model=%s key_index=%s error=%s",
                                       model_name, self.current_client_index, type(error).__name__)
                        break
                    logger.warning("Rotating Gemini key model=%s key_index=%s error=%s",
                                   model_name, self.current_client_index, type(error).__name__)
                    self.rotate_key()
            logger.warning("Gemini model exhausted model=%s", model_name)
        raise RuntimeError("All Gemini models and keys exhausted.")

llm = GeminiLLM()
