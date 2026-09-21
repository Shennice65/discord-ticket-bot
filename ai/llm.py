import asyncio
import logging
from google import genai
from google.genai import types
from google.genai.errors import APIError

from config import Config

logger = logging.getLogger(__name__)

class GeminiLLM:
    def __init__(self):
        self.api_keys = Config.GEMINI_API_KEYS
        self.clients = [genai.Client(api_key=key) for key in self.api_keys if key]
        self.current_client_index = 0

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

    async def generate_content(self, model: str, contents: list, config: types.GenerateContentConfig = None) -> types.GenerateContentResponse:
        """Call the Gemini API with automatic key rotation on resource exhaustion."""
        for attempt in range(len(self.clients) + 1):
            client = self.client
            if not client:
                raise RuntimeError("No Gemini API keys configured.")
            
            try:
                # Use the SDK's native async methods
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config
                )
                return response
            except APIError as e:
                # 429 Resource Exhausted
                if e.code == 429 and attempt < len(self.clients):
                    logger.warning(f"Gemini API rate limit hit. Rotating key. (Attempt {attempt+1}/{len(self.clients)})")
                    self.rotate_key()
                    await asyncio.sleep(1) # Small backoff before retry
                    continue
                raise e
            except Exception as e:
                # Pass through other exceptions
                raise e
                
        raise RuntimeError("All Gemini API keys exhausted.")

llm = GeminiLLM()
