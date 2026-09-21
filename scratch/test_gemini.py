import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
# Dummy image bytes (1x1 transparent png)
image_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
response = client.models.generate_content(
    model='gemini-1.5-flash-8b',
    contents=[part, "Describe this image."]
)
print("Response:", response.text)
