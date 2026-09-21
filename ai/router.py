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
            
        await llm.ensure_keys(getattr(self.bot, "db", None))
        if not llm.client:
            await message.reply("Sorry, I had trouble talking to my brain: No Gemini API keys configured.")
            return
            
        request_deadline = time.monotonic() + 90

        async def bounded(awaitable):
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            return await asyncio.wait_for(awaitable, timeout=remaining)

        async with message.channel.typing():
            context = await self.context_builder.build(message)
            contents = []
            
            for exchange in context.exchanges:
                user_turn, bot_turn = prompts.labeled_exchange(exchange)
                contents.extend([
                    types.Content(role="user", parts=[types.Part.from_text(text=user_turn)]),
                    types.Content(role="model", parts=[types.Part.from_text(text=bot_turn)]),
                ])

            parts = []
            if user_text:
                parts.append(types.Part.from_text(text=(
                    f"CURRENT_DISCORD_USER id={context.current.author_id} "
                    f"name={context.current.author_name}\n{user_text}"
                )))
                
            if message.attachments:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    for att in message.attachments:
                        if att.content_type and att.content_type.startswith('image/'):
                            async with session.get(att.url) as resp:
                                if resp.status == 200:
                                    image_data = await resp.read()
                                    parts.append(types.Part.from_bytes(data=image_data, mime_type=att.content_type))
            
            if not parts:
                return
                
            parts.insert(0, types.Part.from_text(text=prompts.context_text(context)))
            contents.append(types.Content(role="user", parts=parts))
            
            async def search_channel_for_image(channel_name: str, keyword: str = None, username: str = None):
                return await self._search_channel_for_image(message, parts, channel_name, keyword, username)

            async def search_database_memory(name: str):
                return await self._search_database_memory(message, name)

            tool_list = [search_channel_for_image, search_database_memory]
            config = types.GenerateContentConfig(
                system_instruction=prompts.system_instruction(context),
                temperature=0.95,
                tools=tool_list
            )
            
            try:
                response = await bounded(llm.generate_content(
                    model="gemini-3.5-flash", 
                    contents=contents,
                    config=config
                ))
            except Exception as error:
                logger.warning("Initial AI generation failed message_id=%s error=%s",
                               message.id, type(error).__name__)
                await message.reply("Sorry, I had trouble talking to my brain right now.")
                return

            function_calls = getattr(response, "function_calls", ()) or ()
            if function_calls:
                for fn_call in function_calls[:2]:
                    fn_name = fn_call.name
                    args = fn_call.args
                    if fn_name == "search_channel_for_image":
                        tool_result = await bounded(self._search_channel_for_image(message, parts, **args))
                    elif fn_name == "search_database_memory":
                        tool_result = await bounded(self._search_database_memory(message, **args))
                    else:
                        tool_result = "Unknown tool call"
                        
                    contents.append(types.Content(
                        role="model",
                        parts=[types.Part.from_function_call(name=fn_name, args=args)]
                    ))
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(name=fn_name, response={"result": tool_result})]
                    ))
                
                try:
                    response = await bounded(llm.generate_content(
                        model="gemini-3.5-flash", 
                        contents=contents,
                        config=config
                    ))
                except Exception as error:
                    logger.warning("Tool follow-up generation failed message_id=%s error=%s",
                                   message.id, type(error).__name__)
                    await message.reply("Sorry, I had trouble finishing that reply.")
                    return

            reply_text = getattr(response, 'text', '')
            if not reply_text:
                return
                
            self.context_builder.tracker.remember_exchange(message, user_text, reply_text)
            
            # Send paginated
            pages = [reply_text[i:i+2000] for i in range(0, len(reply_text), 2000)]
            for i, page in enumerate(pages):
                if i == 0:
                    await message.reply(page)
                else:
                    await message.channel.send(page)
