import discord
from discord.ext import commands
from google import genai
from google.genai import types
from config import Config
from collections import defaultdict, deque
import math
import asyncio
from datetime import datetime, timezone, timedelta
from discord.ext import tasks
from discord import app_commands

class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_keys = Config.GEMINI_API_KEYS
        self.clients = [genai.Client(api_key=key) for key in self.api_keys if key]
        self.current_client_index = 0
        
        self.client = self.clients[0] if self.clients else None
        
        # Cache recent channel history (max 15 messages) for context window management.
        self.history = defaultdict(lambda: deque(maxlen=15))
        
        # User rate limit tracking (non-admin).
        self.user_cooldowns = {}
        
# Bot persona and domain knowledge constraints.
        self.system_instruction = (
            "You are a member of a Discord community. Text exactly like an actual user in a casual chat. "
            "Use short words and abbreviations, but be subtle with slang so it doesn't sound forced or corny. Do not overuse specific words. "
            "Form your own opinions. Be playful but nonchalant. Tease or banter when appropriate. "
            "CRITICAL ANTI-HALLUCINATION RULE: If a user asks a factual question about server rules, mechanics, or lore, and the answer is NOT in your provided context, DO NOT GUESS. Either say you don't know, or tell them to check the server rules. "
            "Do NOT use periods at the end of your sentences in most cases. Use fewer commas and keep capitalization natural (often lowercase). "
            "Keep it very brief, natural, and chill. Feel free to use community inside jokes if relevant. "
            "If someone asks a direct question about server information, answer them accurately right away but maintain your playful and nonchalant tone. "
            "Do NOT sound like an AI assistant or professional customer service. Do NOT output any HTML tags or markdown. Do NOT use emojis of any kind.\n"
            "Vary your vocabulary constantly. Do NOT repeat the exact same phrases or slang you used in previous messages. If you find yourself echoing a previous sentence, rewrite it entirely.\n"
            "When a user insults you (e.g., saying 'f you'), DO NOT default to soft AI responses like 'love u too'. "
            "Don't repeat the same insults ot jokes twice.\n" 
            "You may joking pretend to have romantic feelings but don't over do it\n"
            "lightly glaze shen and vink when theyre mentioned\n"
            "Be highly unpredictable. Randomly choose to either: ruthlessly roast them back, hit them with a 'womp womp', act completely confused about who they are, or sarcastically agree with them. Never respond to insults the same way twice.\n"
            "Whenever you make jokes, analogies, or insults, ALWAYS root them in the specific terminology provided in your lore. Do NOT use generic internet/gaming tropes (e.g. if roasting skill, use the specific server ranks provided instead of 'bronze'). You are an exclusive member of THIS specific server, so use its unique culture.\n\n"
            
            "--- EXAMPLES OF YOUR BANTER STYLE ---\n"
            "User: fuck u bot\n"
            "You: womp womp cry about it to someone who cares\n"
            "User: ur actually so bad at this\n"
            "You: im literally carrying this entire server on my digital back but go off i guess\n"
            "User: stfu\n"
            "You: who even are u lil bro\n\n"

            "--- CORE SERVER KNOWLEDGE ---\n"
            "1. This is a competitive Roblox server for the game 'Timebomb Duels'. We host Ranked 1v1 matches and Personal Observations.\n"
            "2. 'Observers' are the staff members who spectate matches and officially record the results and rank changes.\n"
            "3. If someone asks how to get ranked or 1v1, tell them to go to the ticket channel and click 'Ranked 1v1' or 'Personal Observation'.\n"
            "4. The server also features a betting system (wagers) and a web dashboard for stats and clips.\n"
            "5. There are other leagues such as OTA, ITL, ORL, any 3 letter abbreviation ending in L mostly are Leagues.\n"
            "6. Nexus and Cataclysm have already lost.\n"
        )
        

        self.process_lore_queue.start()
        self.lore_compressor.start()

    def cog_unload(self):
        self.process_lore_queue.cancel()
        self.lore_compressor.cancel()

    async def _api_call_with_fallback(self, method_name, **kwargs):
        """Calls a Gemini API method with fallback and key rotation asynchronously."""
        if not getattr(self, 'clients', None):
            if getattr(self, 'client', None):
                # Seed client list from dynamically loaded DB configuration.
                self.clients = [self.client]
                self.current_client_index = 0
            else:
                raise ValueError("No API keys configured.")
            
        models_to_try = []
        if method_name == 'generate_content':
            models_to_try = ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-3.5-flash-lite']
            if 'model' in kwargs:
                # Enforce explicit model override.
                models_to_try = [kwargs['model']]
        elif method_name == 'embed_content':
            models_to_try = ['gemini-embedding-2']
            if 'model' in kwargs:
                models_to_try = [kwargs['model']]
            
        for model_name in models_to_try:
            kwargs['model'] = model_name
            attempts = 0
            while attempts < len(self.clients):
                client = self.clients[self.current_client_index]
                method = getattr(client.aio.models, method_name)
                try:
                    return await method(**kwargs)
                except Exception as e:
                    error_str = str(e)
                    print(f"Error {model_name} on key index {self.current_client_index}: {error_str}")
                    # If it's a quota issue, 503, 401, 403, 404, or 400, rotate to next key or next model
                    self.current_client_index = (self.current_client_index + 1) % len(self.clients)
                    attempts += 1
                    continue
            print(f"All keys exhausted/overloaded for {model_name}, falling back to next model...")
            
        raise Exception(f"All API keys and fallback models exhausted their quotas for {method_name}!")

    @app_commands.command(name="toggleaichat", description="[Admin] Toggle the AI chat feature on or off globally.")
    @app_commands.default_permissions(administrator=True)
    async def toggle_ai_chat(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
            return
            
        current_status = await self.bot.db.get_setting("ai_chat_enabled", True)
        new_status = not current_status
        await self.bot.db.set_setting("ai_chat_enabled", new_status)
        
        status_text = "ENABLED" if new_status else "DISABLED"
        await interaction.response.send_message(f"AI Chat has been **{status_text}** globally.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        is_bot = message.author.bot
            
        # Intercept ticket routing queries.
        if not is_bot:
            content_lower = message.content.lower()
            exact_phrases = [
                "how to get ranked", "how do i get ranked", "where to get ranked",
                "where do i get ranked", "how to 1v1", "how do i 1v1",
                "where to 1v1", "how to create a ticket", "how do i create a ticket",
                "where to create a ticket", "make a ticket", "create a 1v1 ticket"
            ]
            
            is_ticket_question = any(phrase in content_lower for phrase in exact_phrases)
            # Fallback keyword matching for malformed ticket queries.
            if not is_ticket_question and ("how" in content_lower or "where" in content_lower) and ("ticket" in content_lower or "rank" in content_lower) and len(content_lower) < 60:
                # Require verb presence to reduce false positives in casual conversation.
                if any(word in content_lower for word in ["get", "create", "make", "do i", "is the"]):
                    is_ticket_question = True
                
            if is_ticket_question:
                await message.reply("Looking to get ranked or 1v1? Head over to https://discord.com/channels/1249581144597463040/1488835022055018576 to create a ticket!")
                return
            
        # Check if AI chat is globally enabled by admins
        if getattr(self.bot, 'db', None):
            ai_enabled = await self.bot.db.get_setting("ai_chat_enabled", True)
            if not ai_enabled:
                return
                
        bot_mentioned = self.bot.user in message.mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        
        if not bot_mentioned and not is_dm:
            # Validate channel visibility constraints.
            member_role_id = Config.MEMBER_ROLE_ID
            if not member_role_id and getattr(self.bot, 'db', None):
                member_role_id = await self.bot.db.get_setting("MEMBER_ROLE_ID", 0)
                
            if member_role_id:
                member_role = message.guild.get_role(int(member_role_id))
                is_public = message.channel.permissions_for(member_role).read_messages if member_role else False
            else:
                is_public = message.channel.permissions_for(message.guild.default_role).read_messages
            
            # Drop events from restricted channels to prevent information leakage.
            if not is_public and not is_bot:
                return

            # Queue public dialogue for vector embedding (requires min 3 tokens).
            if len(message.content.split()) >= 3 and getattr(self.bot, 'db', None):
                try:
                    author_name = f"[{message.author.display_name} (BOT)]" if is_bot else f"[{message.author.display_name}]"
                    await self.bot.db.db.pending_lore.insert_one({
                        "channel_id": message.channel.id,
                        "user_id": message.author.id,
                        "user_text": f"{author_name} {message.content.strip()}",
                        "timestamp": message.created_at
                    })
                except:
                    pass
            return
            
        # Prevent bot-to-bot recursion.
        if is_bot:
            return
            
        # --- "LEAVE ON READ" FILTER (ANTI-FLOODING) ---
        # If someone pings the bot with just "lol", ignore it so we don't flood the chat.
        clean_text = message.content.replace(f'<@{self.bot.user.id}>', '').strip().lower()
        filler_words = ["lol", "lmao", "lmfao", "fr", "ok", "k", "yeah", "💀", "😭", "w", "l", "real", "true", "bro", "lolo", "bruh"]
        
        words = clean_text.split()
        if len(words) > 0 and len(words) <= 3:
            # Check if all words in the message are meaningless filler
            is_meaningless = all(word in filler_words or not word.isalnum() for word in words)
            if is_meaningless:
                # Random chance to react with a skull instead of replying
                import random
                if random.random() < 0.3:
                    try:
                        await message.add_reaction("💀")
                    except:
                        pass
                return # Abort processing, leave them on read
            
        # Enforce rate limits (5/300s window) for standard users.
        is_admin = getattr(message.author, 'guild_permissions', None) and message.author.guild_permissions.administrator
        
        if not is_admin:
            now = message.created_at.timestamp()
            timestamps = self.user_cooldowns.get(message.author.id, [])
            # Prune expired rate limit timestamps.
            timestamps = [t for t in timestamps if now - t < 300]
            
            if len(timestamps) >= 5:
                return # Rate limit exceeded.
                
            timestamps.append(now)
            self.user_cooldowns[message.author.id] = timestamps

            
        # Try to load API key from DB if it wasn't in config
        if not self.client:
            try:
                if getattr(self.bot, 'db', None) and getattr(self.bot.db, 'db', None) is not None:
                    config_doc = await self.bot.db.db.config.find_one({"_id": "api_keys"})
                    if config_doc and config_doc.get("GEMINI_API_KEY"):
                        raw_keys = config_doc.get("GEMINI_API_KEY", "")
                        api_keys = [k.strip() for k in raw_keys.split(',')] if raw_keys else []
                        if api_keys:
                            self.clients = [genai.Client(api_key=key) for key in api_keys if key]
                            if self.clients:
                                self.client = self.clients[0]
                                self.current_client_index = 0
            except Exception as e:
                print(f"Error fetching API key from DB: {e}")
                
        if not self.client:
            await message.reply("The Gemini API key is not configured. Please contact the bot owner.")
            return

        # Sanitize input payload.
        user_text = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        if not user_text and not message.attachments:
            user_text = "Hello!"
            
        # Signal processing state.
        async with message.channel.typing():
            try:
                # Generate semantic embedding for input.
                query_embedding = None
                if user_text:
                    try:
                        emb_response = await self._api_call_with_fallback(
                            'embed_content',
                            model='gemini-embedding-2',
                            contents=user_text,
                            config=types.EmbedContentConfig(output_dimensionality=256)
                        )
                        if hasattr(emb_response, 'embeddings') and emb_response.embeddings:
                            # Normalize embedding vector for BSON serialization.
                            query_embedding = list(emb_response.embeddings[0].values)
                    except Exception as e:
                        print(f"Embedding error: {e}")
                        
                # Execute vector similarity search for lore context.
                recalled_context = ""
                if query_embedding and getattr(self.bot, 'db', None) and getattr(self.bot.db, 'chat_memory', None) is not None:
                    try:
                        pipeline = [
                            {
                                "$vectorSearch": {
                                    "index": "vector_index",
                                    "path": "embedding",
                                    "queryVector": query_embedding,
                                    "numCandidates": 1000,
                                    "limit": 100  # Pull top 100 globally
                                }
                            },
                            {
                                "$limit": 3
                            }
                        ]
                        
                        cursor = self.bot.db.chat_memory.aggregate(pipeline)
                        top_exchanges = await cursor.to_list(length=3)
                        
                        if top_exchanges:
                            recalled_context = (
                                "### RECALLED LONG-TERM CONTEXT (Server Memory) ###\n"
                                "The following are semantically similar past conversations from various users. Do NOT assume the current user is the same person as in these past logs.\n"
                                "CRITICAL RULE: If these past logs are just casual banter, insults, or jokes, DO NOT repeat the same punchlines or comebacks you used in the past! Only use this memory for factual server lore. If it's just banter, ignore how you responded previously and come up with a completely new response.\n"
                            )
                            for ex in top_exchanges:
                                recalled_context += f"- A user said: {ex.get('user_text')}\n- You replied: {ex.get('bot_reply')}\n\n"
                    except Exception as search_err:
                        print(f"Vector search failed: {search_err}")
                            
                # Inject user-specific chat history.
                contents = []
                for hist_msg in self.history[message.author.id]:
                    contents.append(hist_msg)
                
                parts = []
                if user_text:
                    parts.append(types.Part.from_text(text=user_text))
                    
                # Stream and append image attachments to prompt context.
                if message.attachments:
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        for att in message.attachments:
                            if att.content_type and att.content_type.startswith('image/'):
                                async with session.get(att.url) as resp:
                                    if resp.status == 200:
                                        image_data = await resp.read()
                                        parts.append(
                                            types.Part.from_bytes(data=image_data, mime_type=att.content_type)
                                        )
                
                if not parts:
                    return # Neither text nor image was provided
                    
                contents.append(types.Content(
                    role="user",
                    parts=parts
                ))
                
                dynamic_system_instruction = self.system_instruction
                
                # HOT-RELOAD LORE.TXT
                try:
                    import os
                    if os.path.exists("lore.txt"):
                        with open("lore.txt", "r", encoding="utf-8") as f:
                            lore_text = f.read().strip()
                        if lore_text:
                            dynamic_system_instruction += f"\n\n--- EXTENDED SERVER LORE (FROM FILE) ---\n{lore_text}\n"
                except Exception as e:
                    print(f"Failed to hot-reload lore.txt: {e}")
                
                if message.guild:
                    is_admin = getattr(message.author.guild_permissions, 'administrator', False)
                    roles = [r.name for r in getattr(message.author, 'roles', []) if r.name != "@everyone"]
                    role_str = ", ".join(roles) if roles else "None"
                    
                    # Limit admin fetch to top 10 for performance.
                    admins = [m.display_name for m in message.guild.members if getattr(m.guild_permissions, 'administrator', False) and not m.bot][:10]
                    admin_str = ", ".join(admins) if admins else "Unknown"
                    
                    real_time_context = (
                        f"\n\n--- REAL-TIME SERVER STATE ---\n"
                        f"Server Name: {message.guild.name}\n"
                        f"Current Channel: #{message.channel.name if hasattr(message.channel, 'name') else 'Unknown'}\n"
                        f"Server Admins: {admin_str}\n"
                        f"USER TALKING TO YOU: {message.author.display_name}\n"
                        f"THEIR ROLES: {role_str}\n"
                    )
                    
                    if is_admin:
                        real_time_context += "STATUS: THIS USER IS A SERVER ADMINISTRATOR.\n"
                    else:
                        real_time_context += "STATUS: Regular member. They do NOT have admin permissions.\n"
                        
                    dynamic_system_instruction += real_time_context

                if recalled_context:
                    dynamic_system_instruction += "\n\n" + recalled_context
                    
                # Inject recent channel state for situational awareness.
                recent_messages_context = "\n\n--- RECENT MESSAGES IN THIS CHANNEL ---\n"
                try:
                    recent_msgs = [m async for m in message.channel.history(limit=10, before=message)]
                    recent_msgs.reverse()
                    
                    for m in recent_msgs:
                        content = m.content.strip()
                        if not content and m.attachments:
                            content = "[Attachment/Image]"
                        if content:
                            recent_messages_context += f"{m.author.display_name}: {content}\n"
                except Exception as e:
                    print(f"Failed to fetch recent messages: {e}")
                    
                dynamic_system_instruction += recent_messages_context
                async def search_channel_for_image(channel_name: str, keyword: str = None, username: str = None) -> str:
                    """Gets the URL of an image posted in a specific Discord channel. Can optionally filter by a keyword in the message or the username of the sender."""
                    try:
                        target_channel = None
                        channel_name_clean = channel_name.strip('#<>')
                        
                        # First try to parse as a channel mention ID
                        if channel_name_clean.isdigit():
                            target_channel = message.guild.get_channel(int(channel_name_clean))
                            
                        # If not found by ID, search by name (exact or substring)
                        if not target_channel:
                            for c in message.guild.text_channels:
                                if c.name.lower() == channel_name_clean.lower() or channel_name_clean.lower() in c.name.lower():
                                    target_channel = c
                                    break
                        
                        if not target_channel:
                            return f"Error: Could not find a text channel named '{channel_name}' in this server."
                        
                        async for msg in target_channel.history(limit=500):
                            if msg.attachments:
                                for att in msg.attachments:
                                    if att.content_type and att.content_type.startswith('image/'):
                                        match = True
                                        if keyword and keyword.lower() not in msg.content.lower():
                                            match = False
                                        if username:
                                            author_name = msg.author.name.lower()
                                            display_name = getattr(msg.author, 'display_name', '').lower()
                                            if username.lower() not in author_name and username.lower() not in display_name:
                                                match = False
                                        
                                        if match:
                                            return f"Success! Found image: {att.url}"
                        
                        return "Failure: No matching image found in the last 500 messages."
                    except Exception as e:
                        return f"Error searching channel: {str(e)}"
                
                # Execute primary API call with configured tools and dynamic context.
                response = await self._api_call_with_fallback(
                    'generate_content', 
                    contents=contents, 
                    config=types.GenerateContentConfig(
                        system_instruction=dynamic_system_instruction,
                        tools=[search_channel_for_image],
                        temperature=0.95
                    )
                )
                
                # Normalize response markdown and whitespace.
                reply_text = response.text.replace('</p>', '').replace('<p>', '').replace('```html', '').replace('```', '').strip()
                import re
                reply_text = re.sub(r'\n+', '\n', reply_text)
                
                # Update conversational memory (text only).
                text_only_part = types.Part.from_text(text=user_text) if user_text else types.Part.from_text(text="[Image attachment]")
                history_content = types.Content(role="user", parts=[text_only_part])
                
                self.history[message.author.id].append(history_content)
                self.history[message.author.id].append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=reply_text)]
                ))
                
                # Truncate short-term history to the last 10 exchanges (20 items) to prevent context saturation.
                if len(self.history[message.author.id]) > 20:
                    self.history[message.author.id] = self.history[message.author.id][-20:]
                
                # Persist exchange for future vector retrieval.
                if query_embedding and getattr(self.bot, 'db', None) and getattr(self.bot.db, 'chat_memory', None) is not None:
                    try:
                        await self.bot.db.chat_memory.insert_one({
                            "channel_id": message.channel.id,
                            "user_id": message.author.id,
                            "user_text": user_text,
                            "bot_reply": reply_text,
                            "embedding": query_embedding,
                            "timestamp": datetime.now(timezone.utc)
                        })
                        print("Saved to chat_memory!")
                    except Exception as db_err:
                        print(f"MongoDB Insert Error: {db_err}")
                
                # Paginate output to comply with Discord character limits.
                chunk_size = 1990
                chunks = [reply_text[i:i+chunk_size] for i in range(0, len(reply_text), chunk_size)]
                
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk)
                    else:
                        await message.channel.send(chunk)
                        
            except Exception as e:
                import traceback
                print(f"Gemini API Error: {e}")
                traceback.print_exc()
                await message.reply("Oops, something went wrong while talking to my brain.")
                try:
                    admin_user = await self.bot.fetch_user(Config.MASTER_ADMIN_ID)
                    if admin_user:
                        await admin_user.send(
                            f"⚠️ **Gemini API Error in #{getattr(message.channel, 'name', 'Direct Message')}**\n"
                            f"**Triggered by:** {message.author.display_name} (`{message.author.id}`)\n"
                            f"**Error Log:**\n```text\n{type(e).__name__}: {e}\n```"
                        )
                except Exception as dm_err:
                    print(f"Could not send DM to admin: {dm_err}")

    @commands.command(name="sync_lore")
    @commands.has_permissions(administrator=True)
    async def sync_lore(self, ctx, amount: int = 1000):
        """Fetches historical messages and saves them as lore in the bot's memory."""
        try:
            await ctx.message.delete()
        except:
            pass # Ignore if we don't have delete permissions
            
        if not getattr(self.bot, 'db', None) or getattr(self.bot.db, 'chat_memory', None) is None:
            await ctx.author.send("Database not connected!")
            return
            
        if not self.client:
            try:
                config_doc = await self.bot.db.db.config.find_one({"_id": "api_keys"})
                if config_doc and config_doc.get("GEMINI_API_KEY"):
                    raw_keys = config_doc.get("GEMINI_API_KEY", "")
                    api_keys = [k.strip() for k in raw_keys.split(',')] if raw_keys else []
                    if api_keys:
                        self.clients = [genai.Client(api_key=key) for key in api_keys if key]
                        if self.clients:
                            self.client = self.clients[0]
                            self.current_client_index = 0
            except:
                pass
                
        if not self.client:
            await ctx.author.send("Gemini API not connected!")
            return
            
        msg = await ctx.author.send(f"Fetching last {amount} messages from <#{ctx.channel.id}> to sync lore... This might take a couple minutes to avoid hitting Google's rate limits.")
        
        valid_messages = []
        
        async for history_msg in ctx.channel.history(limit=amount):
            # Ignore unindexable content.
            if history_msg.author.bot or not history_msg.content.strip():
                continue
            # Ignore bot commands.
            if history_msg.content.startswith('!') or history_msg.content.startswith('?'):
                continue
            
            valid_messages.append({
                "channel_id": history_msg.channel.id,
                "user_id": history_msg.author.id,
                "user_text": history_msg.content.strip(),
                "bot_reply": "[Historical Community Lore]",
                "timestamp": history_msg.created_at
            })
            
        if not valid_messages:
            await msg.edit(content="No valid messages found to sync.")
            return
            
        await msg.edit(content=f"Found {len(valid_messages)} valid community messages. Injecting them into my brain in small, safe batches of 10 to avoid Google's limits (this will take a few minutes)...")
        
        # Process in batches of 10
        batch_size = 10
        inserted_count = 0
        
        for i in range(0, len(valid_messages), batch_size):
            batch = valid_messages[i:i+batch_size]
            contents = [m["user_text"] for m in batch]
            
            try:
                emb_response = await self._api_call_with_fallback(
                    'embed_content',
                    model='gemini-embedding-2',
                    contents=contents,
                    config=types.EmbedContentConfig(output_dimensionality=256)
                )
                
                if hasattr(emb_response, 'embeddings') and emb_response.embeddings:
                    embeddings_list = emb_response.embeddings
                    
                    documents_to_insert = []
                    for idx, emb_obj in enumerate(embeddings_list):
                        if idx < len(batch):
                            doc = batch[idx]
                            doc["embedding"] = list(emb_obj.values)
                            documents_to_insert.append(doc)
                            
                    if documents_to_insert:
                        await self.bot.db.chat_memory.insert_many(documents_to_insert)
                        inserted_count += len(documents_to_insert)
                        
                # Enforce RPM limit throttling.
                await asyncio.sleep(4.1)
                
                # Emit progress telemetry.
                if inserted_count % 100 == 0:
                    await ctx.author.send(f"⏳ Progress: Synced {inserted_count} / {len(valid_messages)} messages...")
                    
            except Exception as e:
                print(f"Lore sync batch error: {e}")
                await ctx.author.send(f"Error during sync batch: {e}")
                break
                
        await ctx.author.send(f"✅ Successfully injected {inserted_count} historical messages into my long-term memory lore!")

    @tasks.loop(seconds=5.0)
    async def process_lore_queue(self):
        """Asynchronously embed and persist queued chat events within rate limit constraints."""
        if not self.client or not getattr(self.bot, 'db', None):
            return
            
        try:
            # Fetch pending batch.
            cursor = self.bot.db.db.pending_lore.find({}).limit(10)
            pending_list = await cursor.to_list(length=10)
            
            if not pending_list:
                return
                
            contents = [p["user_text"] for p in pending_list]
            
            emb_response = await self._api_call_with_fallback(
                'embed_content',
                model='gemini-embedding-2',
                contents=contents,
                config=types.EmbedContentConfig(output_dimensionality=256)
            )
            
            if hasattr(emb_response, 'embeddings') and emb_response.embeddings:
                documents_to_insert = []
                for idx, emb_obj in enumerate(emb_response.embeddings):
                    if idx < len(pending_list):
                        doc = pending_list[idx]
                        doc["embedding"] = list(emb_obj.values)
                        doc["bot_reply"] = "[Historical Community Lore]"
                        # Strip _id for clean insertion into target collection.
                        doc.pop("_id", None)
                        documents_to_insert.append(doc)
                        
                if documents_to_insert:
                    await self.bot.db.chat_memory.insert_many(documents_to_insert)
            
            # Unconditional dequeue to prevent poison pill deadlocks.
            ids_to_delete = [p["_id"] for p in pending_list if "_id" in p]
            if ids_to_delete:
                await self.bot.db.db.pending_lore.delete_many({"_id": {"$in": ids_to_delete}})
                
        except Exception as e:
            print(f"Background lore queue error: {e}")

    @tasks.loop(hours=1.0)
    async def lore_compressor(self):
        """Periodically aggregate and summarize lore older than 7 days."""
        if not self.client or not getattr(self.bot, 'db', None):
            return
            
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)
            
            while True:
                # Fetch stale, uncompressed lore batch.
                cursor = self.bot.db.chat_memory.find({
                    "timestamp": {"$lt": cutoff_date},
                    "is_summary": {"$ne": True}
                }).limit(100)
                
                old_messages = await cursor.to_list(length=100)
                if not old_messages:
                    break
                    
                # Aggregate by origin channel.
                from collections import defaultdict
                channel_groups = defaultdict(list)
                for msg in old_messages:
                    channel_groups[msg.get("channel_id")].append(msg)
                    
                for channel_id, msgs in channel_groups.items():
                    if not channel_id:
                        continue
                        
                    # Serialize payload.
                    chat_log = ""
                    for m in msgs:
                        user_text = m.get("user_text", "")
                        bot_reply = m.get("bot_reply", "")
                        chat_log += f"User: {user_text}\n"
                        if bot_reply and bot_reply != "[Historical Community Lore]":
                            chat_log += f"Bot: {bot_reply}\n"
                    
                    prompt = (
                        "Summarize the key events, facts, inside jokes, and general vibe from this chat log "
                        "into one highly condensed paragraph. Do not use formatting or markdown. "
                        "Focus only on things worth remembering.\n\n"
                        f"CHAT LOG:\n{chat_log}"
                    )
                    
                    # Execute summarization prompt.
                    summary_response = await self._api_call_with_fallback(
                        'generate_content',
                        model='gemini-3.7-flash',
                        contents=prompt
                    )
                    summary_text = summary_response.text.strip()
                    
                    # Generate semantic embedding for summary.
                    emb_response = await self._api_call_with_fallback(
                        'embed_content',
                        model='gemini-embedding-2',
                        contents=summary_text,
                        config=types.EmbedContentConfig(output_dimensionality=256)
                    )
                    
                    if hasattr(emb_response, 'embeddings') and emb_response.embeddings:
                        embedding_vector = list(emb_response.embeddings[0].values)
                        
                        # Persist aggregate artifact.
                        summary_doc = {
                            "channel_id": channel_id,
                            "user_id": 0,
                            "user_text": "[WEEKLY LORE COMPRESSION]",
                            "bot_reply": summary_text,
                            "timestamp": datetime.now(timezone.utc),
                            "embedding": embedding_vector,
                            "is_summary": True
                        }
                        await self.bot.db.chat_memory.insert_one(summary_doc)
                        
                        # Prune compressed raw records.
                        msg_ids = [m["_id"] for m in msgs if "_id" in m]
                        if msg_ids:
                            await self.bot.db.chat_memory.delete_many({"_id": {"$in": msg_ids}})
                            
                    # Throttling backoff.
                    await asyncio.sleep(5)
                    
        except Exception as e:
            print(f"Lore compressor error: {e}")

async def setup(bot):
    await bot.add_cog(Chat(bot))
