import os
import aiohttp
from aiohttp import web
import json
import traceback

class AIDashboard:
    def __init__(self, bot):
        self.bot = bot

    async def get_data(self, request):
        chat_cog = self.bot.get_cog("Chat")
        if not chat_cog:
            return web.json_response({"error": "Chat cog not loaded"}, status=503)

        evidence_queue_size = chat_cog.evidence_queue.qsize() if hasattr(chat_cog, "evidence_queue") else 0
        
        # Format memory cache
        memory_cache = {}
        if hasattr(chat_cog.retriever, "memory_cache"):
            for guild_id, records in chat_cog.retriever.memory_cache.items():
                memory_cache[str(guild_id)] = [
                    {"memory_key": r.get("memory_key"), "summary": r.get("summary"), "confidence": r.get("confidence")}
                    for r in records[:20]
                ]

        # Format recent conversations
        recent_convos = {}
        if hasattr(chat_cog.tracker, "conversations"):
            for key, convo in chat_cog.tracker.conversations.items():
                channel_id = key.channel_id
                recent_convos[str(channel_id)] = [
                    {"author": msg.author_name, "content": msg.content[:100]}
                    for msg in convo.messages[-10:]
                ]

        return web.json_response({
            "evidence_queue_size": evidence_queue_size,
            "memory_cache": memory_cache,
            "recent_conversations": recent_convos,
        })

    async def index(self, request):
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>AI Brain Dashboard</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e2e; color: #cdd6f4; margin: 0; padding: 20px; }
                h1 { color: #89b4fa; text-align: center; }
                .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
                .card { background-color: #313244; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
                h2 { color: #f38ba8; margin-top: 0; }
                .metric { font-size: 2em; font-weight: bold; color: #a6e3a1; }
                pre { background-color: #181825; padding: 15px; border-radius: 5px; overflow-x: auto; font-size: 14px; color: #f5e0dc; max-height: 400px; }
            </style>
        </head>
        <body>
            <h1>🧠 AI Brain Dashboard</h1>
            <div class="grid">
                <div class="card">
                    <h2>Pending Evidence Queue</h2>
                    <div class="metric" id="queue-size">Loading...</div>
                    <p>Messages waiting to be summarized into Long-Term Memory.</p>
                </div>
                <div class="card">
                    <h2>Live Short-Term Memory</h2>
                    <pre id="short-term">Loading...</pre>
                </div>
                <div class="card" style="grid-column: span 2;">
                    <h2>Long-Term Memory Cache (Top 20)</h2>
                    <pre id="long-term">Loading...</pre>
                </div>
            </div>

            <script>
                async function update() {
                    try {
                        const res = await fetch('/api/data');
                        const data = await res.json();
                        
                        document.getElementById('queue-size').innerText = data.evidence_queue_size;
                        document.getElementById('short-term').innerText = JSON.stringify(data.recent_conversations, null, 2);
                        document.getElementById('long-term').innerText = JSON.stringify(data.memory_cache, null, 2);
                    } catch (e) {
                        console.error(e);
                    }
                }
                setInterval(update, 2000);
                update();
            </script>
        </body>
        </html>
        """
        return web.Response(text=html, content_type='text/html')

    async def post_roblox_match(self, request):
        try:
            data = await request.json()
            roblox_cog = self.bot.get_cog("RobloxStats")
            if roblox_cog:
                await roblox_cog.process_new_match(data)
                return web.json_response({"status": "success"})
            else:
                return web.json_response({"error": "RobloxStats cog not loaded"}, status=503)
        except Exception as e:
            traceback.print_exc()
            return web.json_response({"error": str(e)}, status=500)

    async def post_roblox_live(self, request):
        try:
            data = await request.json()
            roblox_cog = self.bot.get_cog("RobloxStats")
            if roblox_cog:
                await roblox_cog.process_live_update(data)
                return web.json_response({"status": "success"})
            else:
                return web.json_response({"error": "RobloxStats cog not loaded"}, status=503)
        except Exception as e:
            traceback.print_exc()
            return web.json_response({"error": str(e)}, status=500)

    async def roblox_login(self, request):
        discord_id = request.query.get('discord_id', '')
        client_id = os.environ.get("ROBLOX_CLIENT_ID")
        redirect_uri = os.environ.get("ROBLOX_REDIRECT_URI", "https://atlclips.site/api/roblox/callback")
        if not client_id:
            return web.Response(text="ROBLOX_CLIENT_ID not set in .env", status=500)
            
        # Redirect user to Roblox authorization page
        url = f"https://apis.roblox.com/oauth/v1/authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&scope=openid%20profile"
        if discord_id:
            url += f"&state={discord_id}"
            
        raise web.HTTPFound(url)

    async def roblox_callback(self, request):
        code = request.query.get('code')
        discord_id = request.query.get('state') # This is the discord_id we passed in roblox_login
        
        if not code:
            return web.Response(text="No code provided by Roblox.", status=400)
            
        client_id = os.environ.get("ROBLOX_CLIENT_ID")
        client_secret = os.environ.get("ROBLOX_CLIENT_SECRET")
        redirect_uri = os.environ.get("ROBLOX_REDIRECT_URI", "https://atlclips.site/api/roblox/callback")
        
        if not client_id or not client_secret:
            return web.Response(text="ROBLOX_CLIENT_ID or ROBLOX_CLIENT_SECRET not set in .env", status=500)
        
        # Exchange the authorization code for an access token
        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(client_id, client_secret)
            data = {
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri
            }
            async with session.post('https://apis.roblox.com/oauth/v1/token', data=data, auth=auth) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return web.Response(text=f"Failed to get token: {text}", status=500)
                token_data = await resp.json()
                
            access_token = token_data.get('access_token')
            
            # Fetch the user's Roblox profile info
            headers = {'Authorization': f'Bearer {access_token}'}
            async with session.get('https://apis.roblox.com/oauth/v1/userinfo', headers=headers) as resp:
                if resp.status != 200:
                    return web.Response(text="Failed to get user info.", status=500)
                user_info = await resp.json()
                
        # TODO: Save to database!
        # Example: await self.bot.db.users.update_one({"_id": discord_id}, {"$set": {"roblox_id": user_info['sub'], "roblox_name": user_info['preferred_username']}})
        
        return web.json_response({
            "message": "Successfully linked Roblox account!",
            "discord_id_linked": discord_id,
            "roblox_info": user_info
        })

async def start_web_server(bot, port=8080):
    dashboard = AIDashboard(bot)
    app = web.Application()
    app.router.add_get('/', dashboard.index)
    app.router.add_get('/api/data', dashboard.get_data)
    app.router.add_post('/api/roblox/match-stats', dashboard.post_roblox_match)
    app.router.add_post('/api/roblox/live-update', dashboard.post_roblox_live)
    
    # Roblox OAuth Routes
    app.router.add_get('/api/roblox/login', dashboard.roblox_login)
    app.router.add_get('/api/roblox/callback', dashboard.roblox_callback)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web dashboard running on http://localhost:{port}")
