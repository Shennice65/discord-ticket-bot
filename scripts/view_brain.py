import os
import sys
import asyncio
import webbrowser
from dotenv import load_dotenv

# Add the parent directory to sys.path so we can import config if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGO_URI = os.environ.get('MONGO_URI')
MONGO_DB_NAME = os.environ.get('MONGO_DB_NAME', 'discord_bot_db')

if not MONGO_URI:
    print("Error: MONGO_URI not found in environment or .env file.")
    print("Make sure your .env file is in the root directory!")
    input("Press Enter to exit...")
    sys.exit(1)

async def index(request):
    db = request.app['db']
    
    # Fetch recent long-term memories
    memories_cursor = db.chat_memory.find().sort("timestamp", -1).limit(50)
    memories = await memories_cursor.to_list(length=50)
    
    # Fetch recent short-term messages saved to db
    messages_cursor = db.chat_messages.find().sort("created_at", -1).limit(100)
    messages = await messages_cursor.to_list(length=100)

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Standalone AI Brain (Database)</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e2e; color: #cdd6f4; margin: 0; padding: 20px; }
            h1, h2 { color: #89b4fa; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            .card { background-color: #313244; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
            pre { background-color: #181825; padding: 15px; border-radius: 5px; overflow-y: auto; font-size: 14px; color: #f5e0dc; max-height: 600px; }
            .memory-item { border-bottom: 1px solid #45475a; padding-bottom: 10px; margin-bottom: 10px; }
        </style>
    </head>
    <body>
        <h1>🧠 AI Brain Dashboard (Database Viewer)</h1>
        <p>This viewer pulls data directly from your MongoDB database, meaning it shows exactly what the AI has permanently learned.</p>
        <div class="grid">
            <div class="card">
                <h2>Top 50 Long-Term Memories</h2>
                <pre>"""
    for m in memories:
        html += f"<div class='memory-item'><b>Key:</b> {m.get('memory_key')} | <b>Confidence:</b> {m.get('confidence')}<br>"
        html += f"<b>Summary:</b> {m.get('summary')}</div>"
    
    html += """</pre>
            </div>
            <div class="card">
                <h2>Recent Chat Evidence (Raw Database)</h2>
                <pre>"""
    for msg in messages:
        author = msg.get("user_text", "").split("]")[0].replace("[", "") if "user_text" in msg else str(msg.get("author_id", "Unknown"))
        content = msg.get("content", "")
        html += f"<div class='memory-item'><b>{author}:</b> {content}</div>"

    html += """</pre>
            </div>
        </div>
        
        <script>
            // Refresh the page automatically every 5 seconds to keep data live
            setTimeout(function() { location.reload(); }, 5000);
        </script>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def init_app():
    app = web.Application()
    client = AsyncIOMotorClient(MONGO_URI)
    app['db'] = client[MONGO_DB_NAME]
    app.router.add_get('/', index)
    return app

if __name__ == '__main__':
    port = 8081
    print(f"Connecting to MongoDB using URI from .env...")
    
    # We will run a quick async function to print memories to the console
    async def print_memories():
        client = AsyncIOMotorClient(MONGO_URI)
        db = client[MONGO_DB_NAME]
        memories = await db.chat_memory.find().sort("timestamp", -1).limit(10).to_list(length=10)
        print("\n--- TOP 10 RECENT LONG-TERM MEMORIES ---")
        for m in memories:
            print(f"[{m.get('memory_key')}] (Conf: {m.get('confidence')}): {m.get('summary')}")
        print("------------------------------------------\n")
        
    asyncio.get_event_loop().run_until_complete(print_memories())
    
    print(f"Starting local dashboard on http://localhost:{port}...")
    # Open browser automatically after a short delay
    asyncio.get_event_loop().call_later(1.5, lambda: webbrowser.open(f'http://localhost:{port}'))
    
    try:
        web.run_app(init_app(), port=port)
    except KeyboardInterrupt:
        print("Shutting down viewer...")
