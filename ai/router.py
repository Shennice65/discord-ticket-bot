import asyncio
import logging
import time
import random
import re
import aiohttp
from google.genai import types

from ai.llm import llm
from ai import prompts
from context.context_builder import ContextBuilder

logger = logging.getLogger(__name__)

class AIRouter:
    def __init__(self, bot, context_builder: ContextBuilder):
        self.bot = bot
        self.context_builder = context_builder
        self.user_cooldowns = {}
        
    def _is_direct_question(self, content):
        content = (content or "").strip().lower()
        if "?" not in content:
            return False
        bot_name = (getattr(self.bot.user, "display_name", "") or getattr(self.bot.user, "name", "")).lower()
        return any(token in content for token in ("bot", bot_name) if token)

    @staticmethod
    def _is_image_request(content):
        return bool(re.search(
            r"\b(?:image|picture|pic|photo|screenshot)\b|\blook\s+like\b|\bshow\s+me\b",
            (content or "").casefold(),
        ))

    def _needs_memory_lookup(self, message, context):
        """Offer the slower DB fallback only for explicit fact questions on a cache miss."""
        if message.guild is None or getattr(context, "memories", None):
            return False
        retriever = getattr(self.context_builder, "retriever", None)
        if not getattr(retriever, "_memory_cache_channel_id", None):
            return False
        content = (message.content or "").casefold()
        return bool(re.search(
            r"\b(?:who\s+(?:is|was)|who\s+\w{2,}\s+is|what\s+do\s+you\s+know\s+about|"
            r"tell\s+me\s+about|what\s+happened\s+to|why\s+is)\b",
            content,
        ))

    def _memory_lookup_name(self, message):
        """Extract a small human/member name for a scoped cache-miss lookup."""
        for mentioned in getattr(message, "mentions", ()):
            if (getattr(mentioned, "id", None) != getattr(self.bot.user, "id", None)
                    and not getattr(mentioned, "bot", False)):
                return (getattr(mentioned, "display_name", None)
                        or getattr(mentioned, "name", None))

        content = (message.content or "").strip()
        patterns = (
            r"\bwho\s+(?:is|was)\s+([A-Za-z0-9][A-Za-z0-9 _-]{1,59}?)(?:\?|$)",
            r"\bwho\s+([A-Za-z0-9][A-Za-z0-9 _-]{1,59}?)\s+is\b",
            r"\b(?:what\s+do\s+you\s+know\s+about|tell\s+me\s+about|"
            r"what\s+happened\s+to|why\s+is)\s+([A-Za-z0-9][A-Za-z0-9 _-]{1,59}?)(?:\?|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                name = match.group(1).strip(" .,?!")
                normalized = re.sub(r"\s+", " ", name.casefold())
                if (normalized in {"this", "that", "them", "him", "her", "who"}
                        or normalized.startswith(("this person", "that person", "the person"))):
                    return None
                return name
        return None

    @staticmethod
    def _tool_declarations(include_image, include_memory):
        declarations = []
        if include_image:
            declarations.append(types.FunctionDeclaration(
                name="search_channel_for_image",
                description="Find a recent image in a channel the requester can view.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "channel_name": {"type": "string"},
                        "keyword": {"type": "string"},
                        "username": {"type": "string"},
                    },
                    "required": ["channel_name"],
                },
            ))
        if include_memory:
            declarations.append(types.FunctionDeclaration(
                name="search_database_memory",
                description="Find a specific scoped community fact for a named member.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ))
        return [types.Tool(function_declarations=declarations)] if declarations else []
        
    async def _is_reply_to_bot(self, message):
        parent = await self.context_builder.tracker.resolve_reply_parent(message)
        return parent is not None and parent.author_id == self.bot.user.id

    async def _search_channel_for_image(self, message, parts, channel_name, keyword=None, username=None):
        """Search only channels visible to the requesting Discord user."""
        try:
            if message.guild is None:
                return "Error: image search is only available inside a server."
            target_channel = None
            channel_name_clean = channel_name.strip('#<>')
            if channel_name_clean.isdigit():
                target_channel = message.guild.get_channel(int(channel_name_clean))
            if not target_channel:
                for channel in message.guild.text_channels:
                    if channel.name.lower() == channel_name_clean.lower() or channel_name_clean.lower() in channel.name.lower():
                        target_channel = channel
                        break
            if not target_channel:
                return f"Error: Could not find a text channel named '{channel_name}'."
            if not getattr(target_channel.permissions_for(message.author), "view_channel", False):
                return "Error: You cannot view that channel."

            async for found_message in target_channel.history(limit=500):
                for attachment in getattr(found_message, "attachments", ()):
                    if not attachment.content_type or not attachment.content_type.startswith("image/"):
                        continue
                    if keyword and keyword.lower() not in (found_message.content or "").lower():
                        continue
                    if username:
                        author_name = getattr(found_message.author, "name", "").lower()
                        display_name = getattr(found_message.author, "display_name", "").lower()
                        if username.lower() not in author_name and username.lower() not in display_name:
                            continue
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                        async with session.get(attachment.url) as response:
                            if response.status == 200:
                                parts.append(types.Part.from_bytes(
                                    data=await response.read(), mime_type=attachment.content_type
                                ))
                    return f"Success! The image from {attachment.url} has been attached to your vision context."
            return "Failure: No matching image found in the last 500 messages."
        except Exception as error:
            logger.warning("Image search failed message_id=%s error=%s", message.id, type(error).__name__)
            return "Error: image search is temporarily unavailable."

    async def _search_database_memory(self, message, name):
        """Read only confident memories scoped to the current guild and memory channel."""
        try:
            if message.guild is None:
                return "No server-scoped community memory is available in DMs."
            memory_channel_id = self.context_builder.retriever._memory_cache_channel_id
            if memory_channel_id is None:
                return f"No scoped database lore found for '{name}'."
            collection = self.bot.db.db.chat_memory
            cursor = collection.find({
                "guild_id": message.guild.id,
                "channel_id": memory_channel_id,
                "confidence": {"$gte": 0.5},
                "associated_users": {"$regex": re.escape(name), "$options": "i"},
            }).sort("timestamp", -1).limit(5)
            records = await cursor.to_list(length=5)
            if not records:
                return f"No database lore found for '{name}'."
            return "Database Lore for {}:\n{}".format(
                name, "\n".join(f"- {record.get('summary', '')}" for record in records)
            )
        except Exception as error:
            logger.warning("Scoped memory tool failed guild_id=%s error=%s",
                           getattr(message.guild, "id", None), type(error).__name__)
            return "Scoped community memory is temporarily unavailable."

    async def handle_message(self, message, ai_chat_enabled, member_role_id):
        request_started = time.perf_counter()
        if not ai_chat_enabled:
            return

        is_bot = message.author.bot
        self.context_builder.tracker.observe(message)
        if is_bot:
            return
            
        bot_mentioned = self.bot.user in message.mentions
        is_dm = str(message.channel.type) == 'private'
        is_reply_to_bot = await self._is_reply_to_bot(message)
        is_direct_question = self._is_direct_question(message.content)
        
        is_direct_interaction = bot_mentioned or is_reply_to_bot or is_direct_question
        if not is_dm and not is_direct_interaction:
            if member_role_id:
                member_role = message.guild.get_role(int(member_role_id))
                is_public = message.channel.permissions_for(member_role).read_messages if member_role else False
            else:
                is_public = message.channel.permissions_for(message.guild.default_role).read_messages
            if not is_public:
                return

        if not bot_mentioned and not is_dm and not is_reply_to_bot and not is_direct_question:
            return
        logger.info("AI stage message_id=%s stage=routing duration_ms=%d",
                    message.id, (time.perf_counter() - request_started) * 1000)

        # "LEAVE ON READ" FILTER
        clean_text = message.content.replace(f'<@{self.bot.user.id}>', '').strip().lower()
        filler_words = ["lol", "lmao", "lmfao", "fr", "ok", "k", "yeah", "💀", "😭", "w", "l", "real", "true", "bro", "lolo", "bruh"]
        words = clean_text.split()
        if len(words) > 0 and len(words) <= 3:
            if all(word in filler_words or not word.isalnum() for word in words):
                if random.random() < 0.3:
                    try:
                        await message.add_reaction("💀")
                    except:
                        pass
                return
                
        # RATE LIMIT
        is_admin = getattr(message.author, 'guild_permissions', None) and message.author.guild_permissions.administrator
        if not is_admin:
            now = message.created_at.timestamp()
            timestamps = self.user_cooldowns.get(message.author.id, [])
            timestamps = [t for t in timestamps if now - t < 300]
            if len(timestamps) >= 5:
                return
            timestamps.append(now)
            self.user_cooldowns[message.author.id] = timestamps

        user_text = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        if not user_text and not message.attachments:
            user_text = "Hello!"

        if not llm.client:
            await message.reply("Sorry, I had trouble talking to my brain: No Gemini API keys configured.")
            return
            
        request_deadline = time.monotonic() + 90

        async def bounded(awaitable, timeout=None):
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                close = getattr(awaitable, "close", None)
                if close:
                    close()
                raise asyncio.TimeoutError()
            return await asyncio.wait_for(awaitable, timeout=min(remaining, timeout or remaining))

        async with message.channel.typing():
            stage_started = time.perf_counter()
            try:
                context = await bounded(self.context_builder.build(message))
            except Exception as error:
                logger.warning("AI context failed message_id=%s error=%s", message.id, type(error).__name__)
                await bounded(message.reply("Sorry, I couldn't load the conversation context."))
                return
            logger.info("AI stage message_id=%s stage=context duration_ms=%d",
                        message.id, (time.perf_counter() - stage_started) * 1000)
            contents = []
            
            for exchange in context.exchanges:
                user_turn, bot_turn = prompts.labeled_exchange(exchange)
                contents.extend([
                    types.Content(role="user", parts=[types.Part.from_text(text=user_turn)]),
                    types.Content(role="model", parts=[types.Part.from_text(text=bot_turn)]),
                ])

            memory_evidence = None
            if self._needs_memory_lookup(message, context):
                lookup_name = self._memory_lookup_name(message)
                if lookup_name:
                    stage_started = time.perf_counter()
                    try:
                        memory_evidence = await bounded(
                            self._search_database_memory(message, lookup_name),
                            timeout=1.5,
                        )
                    except Exception as error:
                        logger.warning(
                            "AI memory lookup failed message_id=%s error=%s",
                            message.id, type(error).__name__,
                        )
                    logger.info(
                        "AI stage message_id=%s stage=memory_lookup duration_ms=%d",
                        message.id, (time.perf_counter() - stage_started) * 1000,
                    )

            parts = []
            if memory_evidence:
                parts.append(types.Part.from_text(text=(
                    "SCOPED_MEMORY_LOOKUP (uncertain community evidence; do not "
                    "override verified metadata)\n" + str(memory_evidence)[:4000]
                )))
            if user_text:
                parts.append(types.Part.from_text(text=(
                    f"CURRENT_DISCORD_USER id={context.current.author_id} "
                    f"name={context.current.author_name}\n{user_text}"
                )))
                
            stage_started = time.perf_counter()
            try:
                async def load_attachments():
                    if not message.attachments:
                        return
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                        for att in message.attachments:
                            if att.content_type and att.content_type.startswith('image/'):
                                async with session.get(att.url) as resp:
                                    if resp.status == 200:
                                        image_data = await resp.read()
                                        parts.append(types.Part.from_bytes(data=image_data, mime_type=att.content_type))
                await bounded(load_attachments(), timeout=10)
            except Exception as error:
                logger.warning("AI attachment loading failed message_id=%s error=%s",
                               message.id, type(error).__name__)
                await bounded(message.reply("Sorry, I couldn't load that attachment."))
                return
            logger.info("AI stage message_id=%s stage=attachments duration_ms=%d",
                        message.id, (time.perf_counter() - stage_started) * 1000)
            
            if not parts:
                return
                
            parts.insert(0, types.Part.from_text(text=prompts.context_text(context)))
            contents.append(types.Content(role="user", parts=parts))
            
            tool_list = self._tool_declarations(
                self._is_image_request(user_text),
                False,
            )
            config_kwargs = {
                "system_instruction": prompts.system_instruction(context),
                "temperature": 0.82,
                "tools": tool_list,
            }
            if tool_list:
                config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
            config = types.GenerateContentConfig(**config_kwargs)
            generation_started = time.perf_counter()
            try:
                response = await bounded(llm.generate_content(
                    model="gemini-3.5-flash", 
                    contents=contents,
                    config=config
                ))
            except Exception as error:
                logger.warning("Initial AI generation failed message_id=%s error=%s",
                               message.id, type(error).__name__)
                await bounded(message.reply("Sorry, I had trouble talking to my brain right now."))
                return
            logger.info("AI stage message_id=%s stage=generation duration_ms=%d model=%s",
                        message.id, (time.perf_counter() - generation_started) * 1000, "gemini-3.5-flash")

            function_calls = getattr(response, "function_calls", ()) or ()
            if function_calls:
                tool_started = time.perf_counter()
                for fn_call in function_calls[:2]:
                    fn_name = fn_call.name
                    args = fn_call.args
                    try:
                        if fn_name == "search_channel_for_image":
                            tool_result = await bounded(self._search_channel_for_image(message, parts, **args), timeout=10)
                        elif fn_name == "search_database_memory":
                            tool_result = await bounded(self._search_database_memory(message, **args), timeout=10)
                        else:
                            tool_result = "Unknown tool call"
                    except Exception as error:
                        logger.warning("AI tool failed message_id=%s tool=%s error=%s",
                                       message.id, fn_name, type(error).__name__)
                        tool_result = "The requested tool was unavailable."
                        
                    # Feed the result back as bounded labeled context. This keeps
                    # the final request tool-free and works across SDK versions
                    # that differ in function-response role validation.
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_text(
                            text=f"TOOL_RESULT name={fn_name}\n{str(tool_result)[:4000]}"
                        )]
                    ))
                
                logger.info("AI stage message_id=%s stage=tools duration_ms=%d count=%d",
                            message.id, (time.perf_counter() - tool_started) * 1000,
                            min(len(function_calls), 2))
                final_config = types.GenerateContentConfig(
                    system_instruction=prompts.system_instruction(context),
                    temperature=0.82,
                    tools=[],
                )
                try:
                    response = await bounded(llm.generate_content(
                        model="gemini-3.5-flash", 
                        contents=contents,
                        config=final_config
                    ))
                except Exception as error:
                    logger.warning("Tool follow-up generation failed message_id=%s error=%s",
                                   message.id, type(error).__name__)
                    await bounded(message.reply("Sorry, I had trouble finishing that reply."))
                    return

            reply_text = getattr(response, 'text', '')
            if not reply_text:
                await bounded(message.reply("Sorry, I couldn't produce a reply this time."))
                return
            
            # Send paginated
            pages = [reply_text[i:i+2000] for i in range(0, len(reply_text), 2000)]
            send_started = time.perf_counter()
            try:
                for i, page in enumerate(pages):
                    if i == 0:
                        await bounded(message.reply(page))
                        self.context_builder.tracker.remember_exchange(message, user_text, reply_text)
                    else:
                        await bounded(message.channel.send(page))
            except Exception as error:
                logger.warning("AI reply send failed message_id=%s error=%s",
                               message.id, type(error).__name__)
                return
            logger.info("AI stage message_id=%s stage=send duration_ms=%d total_ms=%d",
                        message.id, (time.perf_counter() - send_started) * 1000,
                        (time.perf_counter() - request_started) * 1000)
