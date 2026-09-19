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

class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = Config.GEMINI_API_KEY
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        
        # Store recent history per channel. Limit to last 15 messages to save tokens.
        self.history = defaultdict(lambda: deque(maxlen=15))
        
        # Track cooldowns for non-admin users to prevent spam
        self.user_cooldowns = {}
        
        # System instructions to give the bot a persona
        self.system_instruction = (
            "You are a member of a Discord community. Text exactly like an actual user in a casual chat. "
            "Use short words and abbreviations, but be subtle with slang so it doesn't sound forced or corny. Do not overuse specific words. "
            "Form your own opinions. Do not always agree with the user or act like a 'glazing machine'. Be playful but nonchalant. Tease or banter when appropriate. "
            "Do NOT use periods at the end of your sentences in most cases. Use fewer commas and keep capitalization natural (often lowercase). "
            "Keep it very brief, natural, and chill. Feel free to use community inside jokes if relevant. "
            "If the user asks a direct question about server information (like who the admins are), answer them accurately but keep your casual/troll tone. "
            "Do NOT sound like an AI assistant or professional customer service. Do NOT output any HTML tags or markdown. Do NOT use emojis of any kind.\n\n"
            "--- CORE SERVER KNOWLEDGE ---\n"
            "1. This is a competitive Roblox server for the game 'Timebomb Duels'. We host Ranked 1v1 matches and Personal Observations.\n"
            "2. 'Observers' are the staff members who spectate matches and officially record the results and rank changes.\n"
            "3. If someone asks how to get ranked or 1v1, tell them to go to the ticket channel and click 'Ranked 1v1' or 'Personal Observation'.\n"
            "4. The server also features a betting system (wagers) and a web dashboard for stats and clips."
        )
        self.process_lore_queue.start()
        self.lore_compressor.start()

    def cog_unload(self):
        self.process_lore_queue.cancel()
        self.lore_compressor.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore messages from bots
        if message.author.bot:
            return
            
        # Auto-detect ranking and ticket questions
        content_lower = message.content.lower()
        exact_phrases = [
            "how to get ranked", "how do i get ranked", "where to get ranked",
            "where do i get ranked", "how to 1v1", "how do i 1v1",
            "where to 1v1", "how to create a ticket", "how do i create a ticket",
            "where to create a ticket", "make a ticket", "create a 1v1 ticket"
        ]
        
        is_ticket_question = any(phrase in content_lower for phrase in exact_phrases)
        # Catch short variations like "where is the ticket channel?" or "how to get rank"
        if not is_ticket_question and ("how" in content_lower or "where" in content_lower) and ("ticket" in content_lower or "rank" in content_lower) and len(content_lower) < 60:
            # Extra safety check: require an action word so it doesn't trigger on casual chat like "how is your rank?"
            if any(word in content_lower for word in ["get", "create", "make", "do i", "is the"]):
                is_ticket_question = True
            
        if is_ticket_question:
            await message.reply("Looking to get ranked or 1v1? Head over to https://discord.com/channels/1249581144597463040/1488835022055018576 to create a ticket!")
            return
            
        bot_mentioned = self.bot.user in message.mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        
        if not bot_mentioned and not is_dm:
            # OPTION 3: Auto-queue regular conversations into pending memory (min 3 words to avoid spam)
            if len(message.content.split()) >= 3 and getattr(self.bot, 'db', None):
                try:
                    await self.bot.db.db.pending_lore.insert_one({
                        "channel_id": message.channel.id,
                        "user_id": message.author.id,
                        "user_text": message.content.strip(),
                        "timestamp": message.created_at
                    })
                except:
                    pass
            return
            
        # Check permissions and enforce rate limits for non-admins
        is_admin = getattr(message.author, 'guild_permissions', None) and message.author.guild_permissions.administrator
        
        if not is_admin:
            now = message.created_at.timestamp()
            last_used = self.user_cooldowns.get(message.author.id, 0)
            if now - last_used < 120:  # 2 minute cooldown (120 seconds)
                return # Silently ignore to prevent spam
                
            self.user_cooldowns[message.author.id] = now

            
        # Try to load API key from DB if it wasn't in config
        if not self.client:
            try:
                if getattr(self.bot, 'db', None) and getattr(self.bot.db, 'db', None) is not None:
                    config_doc = await self.bot.db.db.config.find_one({"_id": "api_keys"})
                    if config_doc and config_doc.get("GEMINI_API_KEY"):
                        self.api_key = config_doc.get("GEMINI_API_KEY")
                        self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"Error fetching API key from DB: {e}")
                
        if not self.client:
            await message.reply("The Gemini API key is not configured. Please contact the bot owner.")
            return

        # Prepare user prompt, stripping out the bot mention text
        user_text = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        if not user_text and not message.attachments:
            user_text = "Hello!"
            
        # Add a typing indicator while processing
        async with message.channel.typing():
            try:
                # 1. Get embedding for current user message
                query_embedding = None
                if user_text:
                    try:
                        emb_response = self.client.models.embed_content(
                            model='gemini-embedding-2',
                            contents=user_text,
                            config=types.EmbedContentConfig(output_dimensionality=256)
                        )
                        if hasattr(emb_response, 'embeddings') and emb_response.embeddings:
                            # Convert to a standard Python list so MongoDB can serialize it
                            query_embedding = list(emb_response.embeddings[0].values)
                    except Exception as e:
                        print(f"Embedding error: {e}")
                        
                # 2. Retrieve relevant context from database
                recalled_context = ""
                if query_embedding and getattr(self.bot, 'db', None) and getattr(self.bot.db, 'chat_memory', None) is not None:
                    try:
                        # Use MongoDB Atlas Vector Search for lightning-fast retrieval
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
                                "$match": {
                                    "channel_id": message.channel.id
                                }
                            },
                            {
                                "$limit": 3
                            }
                        ]
                        
                        cursor = self.bot.db.chat_memory.aggregate(pipeline)
                        top_exchanges = await cursor.to_list(length=3)
                        
                        if top_exchanges:
                            recalled_context = "### RECALLED LONG-TERM CONTEXT ###\nThe following are relevant past conversations with this user/channel:\n"
                            for ex in top_exchanges:
                                recalled_context += f"- User said: {ex.get('user_text')}\n- You replied: {ex.get('bot_reply')}\n\n"
                    except Exception as search_err:
                        print(f"Vector search failed: {search_err}")
                            
                # Prepare contents for Gemini
                contents = []
                # Append history
                for hist_msg in self.history[message.channel.id]:
                    contents.append(hist_msg)
                
                # Append current message
                parts = []
                if user_text:
                    parts.append(types.Part.from_text(text=user_text))
                    
                # Download and attach images to the AI prompt
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
                
                # Add recalled context to system instructions
                dynamic_system_instruction = self.system_instruction
                
                # --- NEW REAL-TIME DISCORD CONTEXT ---
                if message.guild:
                    is_admin = getattr(message.author.guild_permissions, 'administrator', False)
                    roles = [r.name for r in getattr(message.author, 'roles', []) if r.name != "@everyone"]
                    role_str = ", ".join(roles) if roles else "None"
                    
                    # Fetch up to 10 admins (bot or human) to save processing
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
                        real_time_context += "STATUS: THIS USER IS A SERVER ADMINISTRATOR! But do NOT glaze them or act overly respectful. Treat them like any other user, just know they have the power to ban you.\n"
                    else:
                        real_time_context += "STATUS: Regular member. They do NOT have admin permissions.\n"
                        
                    dynamic_system_instruction += real_time_context

                if recalled_context:
                    dynamic_system_instruction += "\n\n" + recalled_context
                
                # Call Gemini API with the ultra-fast flash-lite model
                try:
                    response = self.client.models.generate_content(
                        model='gemini-3.5-flash-lite',
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=dynamic_system_instruction,
                        )
                    )
                except Exception as api_err:
                    if '503' in str(api_err):
                        print("3.5-flash-lite is overloaded (503). Falling back to 3.7-flash...")
                        response = self.client.models.generate_content(
                            model='gemini-3.7-flash',
                            contents=contents,
                            config=types.GenerateContentConfig(
                                system_instruction=dynamic_system_instruction,
                            )
                        )
                    else:
                        raise api_err
                
                # Clean up the AI's response text and fix awkward gaps between sentences
                reply_text = response.text.replace('</p>', '').replace('<p>', '').replace('```html', '').replace('```', '').strip()
                import re
                reply_text = re.sub(r'\n+', '\n', reply_text)
                
                # Update history (store only the text part of the user's prompt to save tokens)
                text_only_part = types.Part.from_text(text=user_text) if user_text else types.Part.from_text(text="[Image attachment]")
                history_content = types.Content(role="user", parts=[text_only_part])
                
                self.history[message.channel.id].append(history_content)
                self.history[message.channel.id].append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=reply_text)]
                ))
                
                # 3. Save exchange to MongoDB for long-term memory
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
                
                # Send the reply in chunks if it's over the 2000 character limit
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
                await message.reply(f"Oops, something went wrong while talking to my brain.\n**Admin Error Log:** `{type(e).__name__}: {e}`")

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
            # Try to load API key
            try:
                config_doc = await self.bot.db.db.config.find_one({"_id": "api_keys"})
                if config_doc and config_doc.get("GEMINI_API_KEY"):
                    self.api_key = config_doc.get("GEMINI_API_KEY")
                    self.client = genai.Client(api_key=self.api_key)
            except:
                pass
                
        if not self.client:
            await ctx.author.send("Gemini API not connected!")
            return
            
        msg = await ctx.author.send(f"Fetching last {amount} messages from <#{ctx.channel.id}> to sync lore... This might take a couple minutes to avoid hitting Google's rate limits.")
        
        valid_messages = []
        
        async for history_msg in ctx.channel.history(limit=amount):
            # Skip empty messages or bot messages
            if history_msg.author.bot or not history_msg.content.strip():
                continue
            # Skip commands
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
                emb_response = self.client.models.embed_content(
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
                        
                # Sleep for 4.1 seconds to stay safely under the 15 RPM free tier limit
                await asyncio.sleep(4.1)
                
                # Send a progress update every 100 messages so the user knows it's not frozen
                if inserted_count % 100 == 0:
                    await ctx.author.send(f"⏳ Progress: Synced {inserted_count} / {len(valid_messages)} messages...")
                    
            except Exception as e:
                print(f"Lore sync batch error: {e}")
                await ctx.author.send(f"Error during sync batch: {e}")
                break
                
        await ctx.author.send(f"✅ Successfully injected {inserted_count} historical messages into my long-term memory lore!")

    @tasks.loop(seconds=20.0)
    async def process_lore_queue(self):
        """Background task that embeds and saves queued messages to lore without hitting rate limits."""
        if not self.client or not getattr(self.bot, 'db', None):
            return
            
        try:
            # Fetch up to 10 pending messages
            cursor = self.bot.db.db.pending_lore.find({}).limit(10)
            pending_list = await cursor.to_list(length=10)
            
            if not pending_list:
                return
                
            contents = [p["user_text"] for p in pending_list]
            
            emb_response = self.client.models.embed_content(
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
                        # Remove the _id so it gets a new one in chat_memory, or just keep it
                        doc.pop("_id", None)
                        documents_to_insert.append(doc)
                        
                if documents_to_insert:
                    await self.bot.db.chat_memory.insert_many(documents_to_insert)
            
            # Delete the processed messages from the queue regardless of success/fail to avoid getting stuck
            ids_to_delete = [p["_id"] for p in pending_list if "_id" in p]
            if ids_to_delete:
                await self.bot.db.db.pending_lore.delete_many({"_id": {"$in": ids_to_delete}})
                
        except Exception as e:
            print(f"Background lore queue error: {e}")

    @tasks.loop(hours=1.0)
    async def lore_compressor(self):
        """Background task that compresses messages older than 7 days into single summaries."""
        if not self.client or not getattr(self.bot, 'db', None):
            return
            
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)
            
            while True:
                # Find all unsummarized messages older than 7 days (limit 100 per chunk)
                cursor = self.bot.db.chat_memory.find({
                    "timestamp": {"$lt": cutoff_date},
                    "is_summary": {"$ne": True}
                }).limit(100)
                
                old_messages = await cursor.to_list(length=100)
                if not old_messages:
                    break # All caught up!
                    
                # Group by channel_id
                from collections import defaultdict
                channel_groups = defaultdict(list)
                for msg in old_messages:
                    channel_groups[msg.get("channel_id")].append(msg)
                    
                for channel_id, msgs in channel_groups.items():
                    if not channel_id:
                        continue
                        
                    # Format for Gemini
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
                    
                    # Generate summary using the fast text model
                    summary_response = self.client.models.generate_content(
                        model='gemini-3.7-flash',
                        contents=prompt
                    )
                    summary_text = summary_response.text.strip()
                    
                    # Embed summary
                    emb_response = self.client.models.embed_content(
                        model='gemini-embedding-2',
                        contents=summary_text,
                        config=types.EmbedContentConfig(output_dimensionality=256)
                    )
                    
                    if hasattr(emb_response, 'embeddings') and emb_response.embeddings:
                        embedding_vector = list(emb_response.embeddings[0].values)
                        
                        # Insert the compressed summary
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
                        
                        # Delete the old raw messages we just compressed
                        msg_ids = [m["_id"] for m in msgs if "_id" in m]
                        if msg_ids:
                            await self.bot.db.chat_memory.delete_many({"_id": {"$in": msg_ids}})
                            
                    # Sleep a bit to respect rate limits
                    await asyncio.sleep(5)
                    
        except Exception as e:
            print(f"Lore compressor error: {e}")

async def setup(bot):
    await bot.add_cog(Chat(bot))
