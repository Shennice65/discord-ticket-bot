import hmac
import os
import aiohttp
from aiohttp import web
import traceback

from config import Config

class Dashboard:
    def __init__(self, bot):
        self.bot = bot

    async def index(self, request):
        return web.Response(text="OK")

    def _roblox_authorized(self, request):
        # Fail closed: without a configured secret, no match data is accepted.
        secret = Config.ROBLOX_WEBHOOK_SECRET
        provided = request.headers.get("X-Roblox-Secret", "")
        if not secret or not provided:
            return False
        return hmac.compare_digest(provided.encode(), secret.encode())

    async def post_roblox_match(self, request):
        if not self._roblox_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
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
        if not self._roblox_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
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

    async def post_scrim_sync(self, request):
        if not self._roblox_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                return web.json_response({"error": "Invalid payload"}, status=400)
            scrim_cog = self.bot.get_cog("Scrim")
            if not scrim_cog:
                return web.json_response({"error": "Scrim cog not loaded"}, status=503)
            return web.json_response(await scrim_cog.process_scrim_sync(data))
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
    dashboard = Dashboard(bot)
    app = web.Application()
    app.router.add_get('/', dashboard.index)
    app.router.add_post('/api/roblox/match-stats', dashboard.post_roblox_match)
    app.router.add_post('/api/roblox/live-update', dashboard.post_roblox_live)
    app.router.add_post('/api/scrim/sync', dashboard.post_scrim_sync)
    
    # Roblox OAuth Routes
    app.router.add_get('/api/roblox/login', dashboard.roblox_login)
    app.router.add_get('/api/roblox/callback', dashboard.roblox_callback)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web dashboard running on http://localhost:{port}")
