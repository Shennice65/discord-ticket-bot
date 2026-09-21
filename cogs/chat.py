import discord
from discord.ext import commands
from google import genai
from google.genai import types
from config import Config
from core.services.server_brain import ServerBrain
from core.services.memory_extractor import MemoryExtractor
from core.services import chat_prompts
import asyncio
import time
from datetime import datetime, timezone, timedelta
from discord.ext import tasks
from discord import app_commands
import logging

logger = logging.getLogger(__name__)

class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_keys = Config.GEMINI_API_KEYS
        self.clients = [genai.Client(api_key=key) for key in self.api_keys if key]
        self.current_client_index = 0
        
        self.client = self.clients[0] if self.clients else None
        
        self.server_brain = ServerBrain(bot, self._embed_query)
        self.memory_extractor = MemoryExtractor(self._api_call_with_fallback)
        # Environment is the fallback; the live value is refreshed from the
        # MongoDB config document so it can be changed without redeploying.
        self.memory_channel_id = Config.AI_MEMORY_CHANNEL_ID
        self._memory_channel_config_checked_at = 0.0
        self.evidence_queue = asyncio.Queue(maxsize=1000)
        self.ai_chat_enabled = True
        self.member_role_id = Config.MEMBER_ROLE_ID
        
        # User rate limit tracking (non-admin).
        self.user_cooldowns = {}
        
        self.process_lore_queue.start()
        self.persist_evidence_queue.start()
        self.refresh_runtime_config.start()
        self.refresh_lore_cache.start()
        self.lore_compressor.start()

    def cog_unload(self):
        self.process_lore_queue.cancel()
        self.persist_evidence_queue.cancel()
        self.refresh_runtime_config.cancel()
        self.refresh_lore_cache.cancel()
        self.lore_compressor.cancel()

    def _is_memory_channel(self, message: discord.Message) -> bool:
        """Return whether a message may contribute to long-term AI memory."""
        memory_channel_id = getattr(self, "memory_channel_id", Config.AI_MEMORY_CHANNEL_ID)
        return bool(
            memory_channel_id
            and getattr(message.channel, "id", None) == memory_channel_id
            and getattr(message, "guild", None) is not None
        )

    async def _refresh_memory_channel_id(self) -> int:
        """Load the AI memory channel from MongoDB, with an environment fallback."""
        now = time.monotonic()
        if now - getattr(self, "_memory_channel_config_checked_at", 0.0) < 60:
            return getattr(self, "memory_channel_id", Config.AI_MEMORY_CHANNEL_ID)

        self._memory_channel_config_checked_at = now
        fallback = Config.AI_MEMORY_CHANNEL_ID
        db = getattr(self.bot, "db", None)
        config_collection = getattr(getattr(db, "db", None), "config", None) if db else None
        if config_collection is None:
            self.memory_channel_id = fallback
            return self.memory_channel_id

        try:
            config_doc = await config_collection.find_one(
                {"_id": "api_keys"}, {"AI_MEMORY_CHANNEL_ID": 1}
            )
            if not config_doc or config_doc.get("AI_MEMORY_CHANNEL_ID") in (None, ""):
                config_doc = await config_collection.find_one(
                    {"AI_MEMORY_CHANNEL_ID": {"$exists": True}},
                    {"AI_MEMORY_CHANNEL_ID": 1},
                )
            raw_channel_id = config_doc.get("AI_MEMORY_CHANNEL_ID") if config_doc else None
            self.memory_channel_id = int(raw_channel_id) if raw_channel_id not in (None, "") else fallback
        except Exception as error:
            print(f"AI memory channel config lookup error: {error}")
            self.memory_channel_id = fallback
        return self.memory_channel_id

    async def _is_reply_to_bot(self, message: discord.Message) -> bool:
        parent = await self.server_brain.resolve_reply_parent(message)
        return parent is not None and parent.author_id == self.bot.user.id

    def _is_direct_question(self, message: discord.Message) -> bool:
        """Recognize explicit questions addressed to the bot without broad auto-chat."""
        content = (message.content or "").strip().lower()
        if "?" not in content:
            return False
        bot_name = (getattr(self.bot.user, "display_name", "") or getattr(self.bot.user, "name", "")).lower()
        return any(token in content for token in ("bot", bot_name) if token)

    async def _embed_query(self, text):
        response = await self._api_call_with_fallback(
            'embed_content', model='gemini-embedding-2', contents=text,
            config=types.EmbedContentConfig(output_dimensionality=256),
        )
        return list(response.embeddings[0].values) if response.embeddings else None

    async def _record_message_evidence(self, message: discord.Message) -> None:
        """Queue raw general-channel evidence without blocking the reply path."""
        if not self._is_memory_channel(message) or message.author.bot:
            return
        content = (message.content or "").strip()
        if not content or content.startswith(("!", "?")):
            return

        queue = getattr(self, "evidence_queue", None)
        if queue is None:
            return

        reference = getattr(message, "reference", None)
        reply_to_id = getattr(reference, "message_id", None) if reference else None
        evidence = {
            "message_id": message.id,
            "guild_id": message.guild.id,
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "content": content,
            "reply_to_message_id": reply_to_id,
            "created_at": message.created_at,
            "reaction_count": sum(reaction.count for reaction in getattr(message, "reactions", [])),
        }
        if len(content.split()) >= 3:
            evidence["user_text"] = f"[{message.author.display_name}] {content}"
        try:
            queue.put_nowait(evidence)
        except asyncio.QueueFull:
            logger.warning("Chat evidence queue full; dropping message_id=%s", message.id)

    async def _refresh_runtime_config(self):
        await self._refresh_memory_channel_id()
        await self.server_brain.refresh_memory_cache(self.memory_channel_id)
        db = getattr(self.bot, "db", None)
        if not db:
            return
        try:
            self.ai_chat_enabled = await db.get_setting("ai_chat_enabled", True)
            self.member_role_id = await db.get_setting("MEMBER_ROLE_ID", Config.MEMBER_ROLE_ID)
        except Exception as error:
            logger.debug("Runtime config refresh unavailable error=%s", type(error).__name__)

    @tasks.loop(minutes=1)
    async def refresh_runtime_config(self):
        await self._refresh_runtime_config()

    @tasks.loop(minutes=1)
    async def refresh_lore_cache(self):
        await self.server_brain.refresh_lore_cache()

    @tasks.loop(seconds=2)
    async def persist_evidence_queue(self):
        db = getattr(self.bot, "db", None)
        if not db:
            return
        batch = []
        while len(batch) < 25:
            try:
                batch.append(self.evidence_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            return
        try:
            collections = getattr(db, "db", None)
            chat_messages = getattr(db, "chat_messages", None)
            pending_lore = getattr(db, "pending_lore", None)
            if chat_messages is None and collections is not None:
                chat_messages = collections.chat_messages
            if pending_lore is None and collections is not None:
                pending_lore = collections.pending_lore
            if chat_messages is None or pending_lore is None:
                raise RuntimeError("chat evidence collections unavailable")
            for evidence in batch:
                await chat_messages.update_one(
                    {"message_id": evidence["message_id"]}, {"$set": evidence}, upsert=True
                )
                if evidence.get("user_text"):
                    await pending_lore.update_one(
                        {"message_id": evidence["message_id"]},
                        {"$setOnInsert": evidence}, upsert=True,
                    )
                self.evidence_queue.task_done()
        except Exception as error:
            logger.warning("Chat evidence persistence failed; retrying batch error=%s", type(error).__name__)
            for evidence in batch:
                try:
                    self.evidence_queue.put_nowait(evidence)
                except asyncio.QueueFull:
                    break

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
                    return await asyncio.wait_for(method(**kwargs), timeout=15)
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
        self.ai_chat_enabled = new_status
        
        status_text = "ENABLED" if new_status else "DISABLED"
        await interaction.response.send_message(f"AI Chat has been **{status_text}** globally.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        scope = getattr(after.guild, "id", None), after.channel.id
        if after.id in self.server_brain.recent_messages.get(scope, {}):
            self.server_brain.forget(*scope, after.id)
            self.server_brain.observe(after)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        self.server_brain.forget(payload.guild_id, payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        for message_id in payload.message_ids:
            self.server_brain.forget(payload.guild_id, payload.channel_id, message_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        request_started = time.perf_counter()
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
        if not getattr(self, "ai_chat_enabled", True):
            return

        # Bot messages are never learned or answered. Raw evidence is queued
        # only for the configured general channel.
        self.server_brain.observe(message)
        if is_bot:
            return
        await self._record_message_evidence(message)
                
        bot_mentioned = self.bot.user in message.mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_reply_to_bot = await self._is_reply_to_bot(message)
        is_direct_question = self._is_direct_question(message)
        
        # Direct interactions are allowed in any channel the bot can read.
        # Only unaddressed public traffic is subject to the member-role gate.
        is_direct_interaction = bot_mentioned or is_reply_to_bot or is_direct_question
        if not is_dm and not is_direct_interaction:
            # Validate channel visibility constraints.
            member_role_id = getattr(self, "member_role_id", Config.MEMBER_ROLE_ID)
                
            if member_role_id:
                member_role = message.guild.get_role(int(member_role_id))
                is_public = message.channel.permissions_for(member_role).read_messages if member_role else False
            else:
                is_public = message.channel.permissions_for(message.guild.default_role).read_messages
            
            # Drop events from restricted channels to prevent information leakage.
            if not is_public:
                return

        if not bot_mentioned and not is_dm and not is_reply_to_bot and not is_direct_question:
            return

        logger.info("AI timing stage=route seconds=%.3f", time.perf_counter() - request_started)
            
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
                context_started = time.perf_counter()
                context = await self.server_brain.build_context(message)
                logger.info("AI timing stage=context seconds=%.3f", time.perf_counter() - context_started)
                contents = []
                for exchange in context.exchanges:
                    user_turn, bot_turn = chat_prompts.labeled_exchange(exchange)
                    contents.extend([
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=user_turn)],
                        ),
                        types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=bot_turn)],
                        ),
                    ])

                parts = []
                if user_text:
                    parts.append(types.Part.from_text(text=(
                        f"CURRENT_DISCORD_USER id={context.current.author_id} "
                        f"name={context.current.author_name}\n{user_text}"
                    )))
                    
                # Stream and append image attachments to prompt context.
                image_started = time.perf_counter()
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
                if message.attachments:
                    logger.info("AI timing stage=images seconds=%.3f", time.perf_counter() - image_started)
                
                if not parts:
                    return # Neither text nor image was provided

                parts.insert(0, types.Part.from_text(text=chat_prompts.context_text(context)))
                    
                contents.append(types.Content(
                    role="user",
                    parts=parts
                ))
                
                dynamic_system_instruction = chat_prompts.system_instruction(context)
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

                        if not message.guild or not target_channel.permissions_for(message.author).view_channel:
                            return "Error: You cannot view that channel."
                        
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
                generation_started = time.perf_counter()
                response = await asyncio.wait_for(
                    self._api_call_with_fallback(
                        'generate_content',
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=dynamic_system_instruction,
                            tools=[search_channel_for_image],
                            temperature=0.95
                        )
                    ),
                    timeout=90,
                )
                
                # Normalize response markdown and whitespace.
                reply_text = response.text.replace('</p>', '').replace('<p>', '').replace('```html', '').replace('```', '').strip()
                import re
                reply_text = re.sub(r'\n+', '\n', reply_text)
                
                # Paginate output to comply with Discord character limits.
                chunk_size = 1990
                chunks = [reply_text[i:i+chunk_size] for i in range(0, len(reply_text), chunk_size)]
                
                send_started = time.perf_counter()
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk)
                    else:
                        await message.channel.send(chunk)

                logger.info("AI timing stage=reply seconds=%.3f total=%.3f",
                            time.perf_counter() - generation_started, time.perf_counter() - request_started)
                logger.info("AI timing stage=send seconds=%.3f", time.perf_counter() - send_started)

                self.server_brain.remember_exchange(message, user_text or "[Image attachment]", reply_text)
                        
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

        memory_channel_id = await self._refresh_memory_channel_id()
        if not memory_channel_id or ctx.channel.id != memory_channel_id:
            await ctx.author.send("Lore sync is limited to the configured general chat channel.")
            return
            
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
                "guild_id": getattr(ctx.guild, "id", None),
                "channel_id": history_msg.channel.id,
                "message_id": history_msg.id,
                "user_id": history_msg.author.id,
                "user_text": history_msg.content.strip(),
                "bot_reply": "[Historical Community Lore]",
                "timestamp": history_msg.created_at,
            })
            
        if not valid_messages:
            await msg.edit(content="No valid messages found to sync.")
            return
            
        await msg.edit(content=f"Found {len(valid_messages)} valid community messages. Injecting them into my brain in small, safe batches of 10 to avoid Google's limits (this will take a few minutes)...")
        
        # Process conversation chunks through the same extractor used by live learning.
        batch_size = 25
        inserted_count = 0
        
        for i in range(0, len(valid_messages), batch_size):
            batch = valid_messages[i:i+batch_size]
            try:
                succeeded, stored = await self.memory_extractor.process(self.bot.db, batch)
                inserted_count += stored
                        
                await asyncio.sleep(1.0)
                
                # Emit progress telemetry.
                if inserted_count and inserted_count % 10 == 0:
                    await ctx.author.send(f"⏳ Progress: Synced {min(i + len(batch), len(valid_messages))} / {len(valid_messages)} messages...")
                    
            except Exception as e:
                print(f"Lore sync batch error: {e}")
                await ctx.author.send(f"Error during sync batch: {e}")
                break
                
        await ctx.author.send(f"✅ Successfully injected {inserted_count} historical messages into my long-term memory lore!")

    @tasks.loop(minutes=1)
    async def process_lore_queue(self):
        """Asynchronously embed and persist queued chat events within rate limit constraints."""
        if not self.client or not getattr(self.bot, 'db', None):
            return
        memory_channel_id = await self._refresh_memory_channel_id()
        if not memory_channel_id:
            return
            
        try:
            # Fetch only configured general-channel evidence.
            pending_collection = getattr(self.bot.db, "pending_lore", None)
            if pending_collection is None:
                pending_collection = self.bot.db.db.pending_lore
            cursor = pending_collection.find({"channel_id": memory_channel_id}).limit(25)
            pending_list = await cursor.to_list(length=25)
            
            if not pending_list:
                return
                
            succeeded, _stored = await self.memory_extractor.process(self.bot.db, pending_list)

            # Remove only records that were persisted successfully. Failed
            # batches remain queued for a later retry.
            if succeeded:
                await pending_collection.delete_many({"_id": {"$in": [item["_id"] for item in pending_list if item.get("_id") is not None]}})
                
        except Exception as e:
            print(f"Background lore queue error: {e}")

    @tasks.loop(hours=1.0)
    async def lore_compressor(self):
        """Periodically aggregate and summarize lore older than 7 days."""
        if not self.client or not getattr(self.bot, 'db', None):
            return
        memory_channel_id = await self._refresh_memory_channel_id()
        if not memory_channel_id:
            return
            
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)
            
            while True:
                # Fetch stale, uncompressed lore batch.
                cursor = self.bot.db.chat_memory.find({
                    "timestamp": {"$lt": cutoff_date},
                    "is_summary": {"$ne": True},
                    "channel_id": memory_channel_id,
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
                            "record_type": "summary",
                            "guild_id": msgs[0].get("guild_id"),
                            "channel_id": channel_id,
                            "user_id": 0,
                            "user_text": "[WEEKLY LORE COMPRESSION]",
                            "bot_reply": summary_text,
                            "timestamp": datetime.now(timezone.utc),
                            "embedding": embedding_vector,
                            "is_summary": True,
                            "source_message_ids": [
                                source_id
                                for record in msgs
                                for source_id in record.get("source_message_ids", [])
                            ],
                            "confidence": 0.45,
                            "importance": 0.5,
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
