import os
import asyncio
from google import genai
from dotenv import load_dotenv

load_dotenv()

async def test():
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    response = client.models.embed_content(
        model='text-embedding-004',
        contents="Hello world"
    )
    print(dir(response))
    if hasattr(response, 'embeddings'):
        print(len(response.embeddings[0].values))
        print("Success embeddings!")
    else:
        print("No embeddings attr found.")

asyncio.run(test())
