import os
import asyncio
from google import genai
from dotenv import load_dotenv

load_dotenv()

async def test():
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    try:
        emb_response = client.models.embed_content(
            model='text-embedding-004',
            contents="hello"
        )
        vals = emb_response.embeddings[0].values
        print("Type of values:", type(vals))
        print("Is list?", isinstance(vals, list))
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
