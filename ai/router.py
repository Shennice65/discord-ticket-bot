import asyncio
import base64
import json
import logging
import random
import re
import time

from ai import prompts
from ai.llm import GenerationResult, ToolCall, llm
from ai.sidecar import AgentSidecarBridge
from ai.engagement import UserEngagementScorer
from ai.tools import ReadOnlyToolRegistry
from context.context_builder import ContextBuilder
from config import Config
from framework.approvals import ApprovalManager
from framework.audit import record_agent_event
from framework.pipeline import ToolExecutionPipeline

logger = logging.getLogger(__name__)


class AIRouter:
    MAX_TOOL_ROUNDS = 4

    def __init__(self, bot, context_builder: ContextBuilder):
        self.bot = bot
        self.context_builder = context_builder
        self.engagement = UserEngagementScorer(bot)
        self.user_cooldowns = {}
        self._concurrency_limit = asyncio.Semaphore(3)
        self.tools = ReadOnlyToolRegistry(bot, context_builder)
        self.pipeline = ToolExecutionPipeline(
            self.tools,
            bot,
            approvals=ApprovalManager(getattr(Config, "AGENT_APPROVAL_TOOLS", ())),
        )
        self.sidecar = AgentSidecarBridge(bot)

    def _is_direct_question(self, content):
        content = (content or "").strip().lower()
        if "?" not in content:
            return False
        bot_name = (getattr(self.bot.user, "display_name", "") or getattr(self.bot.user, "name", "")).lower()
        return any(token in content for token in ("bot", bot_name) if token)

    @staticmethod
    def _is_image_request(content):
        return bool(re.search(
            r"\b(?:image|picture|pic|photo|screenshot)\b|\blook\s+like\b|\bshow\s+me\b|"
            r"\b(?:see|read|translate|describe)\s+(?:the\s+)?(?:image|picture|photo|one\s+above)\b|"
            r"\b(?:person|user)\s+above\s+(?:saying|doing)\b|\bwhat\s+does\s+.*\s+(?:say|saying)\b",
            (content or "").casefold(),
        ))



    @staticmethod
    def _apply_tool_mentions(text, mention_sources):
        """Turn model references to tool-returned players into real Discord tags."""
        result = text
        for source in mention_sources:
            name = str(source.get("player_name") or "").strip()
            user_id = source.get("user_id")
            mention = str(source.get("player_mention") or "").strip()
            if not mention or not user_id:
                continue
            result = result.replace(f"Discord user {user_id}", mention)
            if name:
                result = re.sub(
                    rf"(?<![\w@]){re.escape(name)}(?![\w])",
                    mention,
                    result,
                    flags=re.IGNORECASE,
                )
        return result

    @staticmethod
    def _image_tool_definition():
        return {
            "type": "function",
            "function": {
                "name": "search_channel_for_image",
                "description": "Find a recent image in a channel the requester can view.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_name": {"type": "string"},
                        "keyword": {"type": "string"},
                        "username": {"type": "string"},
                    },
                    "required": ["channel_name"],
                    "additionalProperties": False,
                },
            },
        }

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
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": attachment.url},
                    })
                    return f"Success: the image URL {attachment.url} was attached to the vision context."
            return "Failure: no matching image found in the last 500 messages."
        except Exception as error:
            logger.warning("Image search failed message_id=%s error=%s", message.id, type(error).__name__)
            return "Error: image search is temporarily unavailable."

    async def _run_image_tool(self, args, message, _context):
        parts = []
        result = await self._search_channel_for_image(
            message,
            parts,
            str(args.get("channel_name", "")),
            keyword=args.get("keyword"),
            username=args.get("username"),
        )
        if parts:
            result = f"{result} {parts[0].get('image_url', {}).get('url', '')}"
        return result

    async def _generate(self, messages, tools=None, max_tokens=None, temperature=0.6):
        """Call the new adapter while retaining compatibility with old test doubles."""
        if max_tokens is None:
            max_tokens = getattr(Config, "AI_MAX_OUTPUT_TOKENS", 600)
            
        if hasattr(llm, "generate"):
            return await llm.generate(
                messages, tools=tools, temperature=temperature,
                max_tokens=max_tokens,
            )
        response = await llm.generate_content(
            model="openrouter",
            contents=json.dumps(messages, ensure_ascii=False),
        )
        legacy_calls = tuple(
            ToolCall(call_id="", name=call.name, arguments=getattr(call, "args", {}) or {})
            for call in (getattr(response, "function_calls", ()) or ())
        )
        return GenerationResult(text=getattr(response, "text", ""), tool_calls=legacy_calls)

    async def _load_attachment_parts(self, message):
        parts = []
        for attachment in getattr(message, "attachments", ()):
            content_type = getattr(attachment, "content_type", "") or ""
            if content_type.startswith("image/"):
                try:
                    data = await attachment.read()
                    b64 = base64.b64encode(data).decode("utf-8")
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{content_type};base64,{b64}"},
                    })
                except Exception as e:
                    logger.warning("Failed to read attachment %s: %s", attachment.url, e)
        return parts

    async def handle_message(self, message, ai_chat_enabled, member_role_id):
        request_started = time.perf_counter()
        if not ai_chat_enabled:
            return

        if message.author.bot:
            return
        self.context_builder.tracker.observe(message)

        bot_mentioned = self.bot.user in message.mentions
        is_dm = message.guild is None
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
        if not is_direct_interaction and not is_dm:
            return

        logger.info("AI stage message_id=%s stage=routing duration_ms=%d",
                    message.id, (time.perf_counter() - request_started) * 1000)

        clean_text = message.content.replace(f'<@{self.bot.user.id}>', '').strip().lower()
        filler_words = ["lol", "lmao", "lmfao", "fr", "ok", "k", "yeah", "💀", "😭", "w", "l", "real", "true", "bro", "lolo", "bruh"]
        words = clean_text.split()
        if 0 < len(words) <= 3 and all(word in filler_words or not word.isalnum() for word in words):
            if random.random() < 0.3:
                try:
                    await message.add_reaction("💀")
                except Exception:
                    pass
            return

        is_admin = bool(getattr(getattr(message.author, "guild_permissions", None), "administrator", False))
        if not is_admin:
            now = message.created_at.timestamp()
            timestamps = [t for t in self.user_cooldowns.get(message.author.id, []) if now - t < 300]
            if len(timestamps) >= 5:
                return
            timestamps.append(now)
            self.user_cooldowns[message.author.id] = timestamps

        profile = await self.engagement.get_profile(
            message.author.id, 
            message.author if hasattr(message.author, "joined_at") else None
        )

        # Check quota first
        db = getattr(self.bot, "db", None)
        if db and getattr(db, "user_quotas", None) is not None:
            quota_state = await db.get_user_quota(message.author.id)
            limit = quota_state.get("limit_override")
            if limit is None:
                limit = int(getattr(Config, "AI_QUOTA_TOKEN_LIMIT", 5000) * profile.quota_multiplier)
            if limit >= 0 and quota_state.get("tokens_used", 0) >= limit:
                quota_reactions = ["💤", "⏰", "🧊", "😶", "🫠", "🤫"]
                try:
                    await message.add_reaction(random.choice(quota_reactions))
                except Exception:
                    pass
                return

        user_text = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        if not user_text and not message.attachments:
            user_text = "Hello!"

        if hasattr(llm, "ensure_keys"):
            await llm.ensure_keys(getattr(self.bot, "db", None))
        if not llm.client:
            await message.reply("Sorry, I had trouble talking to my brain: no AI provider key is configured.")
            return

        request_deadline = time.monotonic() + getattr(Config, "AI_REQUEST_TIMEOUT_SECONDS", 180)

        async def bounded(awaitable, timeout=None):
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                close = getattr(awaitable, "close", None)
                if close:
                    close()
                raise asyncio.TimeoutError()
            return await asyncio.wait_for(awaitable, timeout=min(remaining, timeout or remaining))

        try:
            await bounded(self._concurrency_limit.acquire(), timeout=45)
        except asyncio.TimeoutError:
            await message.reply("Sorry, I'm a bit overwhelmed right now. Please try again in a minute!")
            return

        try:
            async with message.channel.typing():
                try:
                    context = await bounded(self.context_builder.build(message))
                except Exception as error:
                    logger.warning("AI context failed message_id=%s error=%s", message.id, type(error).__name__)
                    await message.reply("Sorry, I couldn't load the conversation context.")
                    return

                max_history = profile.max_history
                max_tokens = profile.max_tokens

                bot_name = (getattr(self.bot.user, "display_name", "") or getattr(self.bot.user, "name", "this bot"))
                messages = [{
                    "role": "system",
                    "content": prompts.system_instruction(context, bot_name=bot_name, style_hint=profile.style_hint),
                }]
                for exchange in context.exchanges[-max_history:] if max_history > 0 else []:
                    user_turn, bot_turn = prompts.labeled_exchange(exchange)
                    messages.extend([
                        {"role": "user", "content": user_turn},
                        {"role": "assistant", "content": bot_turn},
                    ])

                content_parts = [{
                    "type": "text",
                    "text": prompts.context_text(context) + "\n\nCURRENT_USER_MESSAGE:\n" + user_text,
                }]
                content_parts.extend(await self._load_attachment_parts(message))
                
                if hasattr(message, "reference") and message.reference and getattr(message.reference, "message_id", None):
                    try:
                        ref_msg = message.reference.resolved
                        if not ref_msg and isinstance(message.reference.message_id, int):
                            ref_msg = await message.channel.fetch_message(message.reference.message_id)
                        if ref_msg:
                            content_parts.extend(await self._load_attachment_parts(ref_msg))
                    except Exception as e:
                        logger.warning("Failed to fetch referenced message %s for attachments: %s", message.reference.message_id, e)
                        
                messages.append({
                    "role": "user",
                    "content": content_parts if len(content_parts) > 1 else content_parts[0]["text"],
                })

                tool_definitions = self.tools.definitions
                if self._is_image_request(user_text):
                    image_definition = self._image_tool_definition()
                    tool_definitions.append(image_definition)
                    self.pipeline.register_external_tool(image_definition, self._run_image_tool)

                response = None
                sidecar_response = None
                streamed_reply = None
                streamed_text = []
                mention_sources = []

                def collect_mentions(tool_name, result):
                    if tool_name not in {"get_player_rank", "get_leaderboard"}:
                        return
                    data = result.get("data") if isinstance(result, dict) else None
                    if not isinstance(data, dict):
                        return
                    rows = data.get("players") if tool_name == "get_leaderboard" else [data]
                    for row in rows or ():
                        if isinstance(row, dict) and row.get("player_mention"):
                            mention_sources.append(row)

                async def execute_agent_tool(name, arguments):
                    result = await bounded(
                        self.pipeline.execute(name, arguments, message, context),
                        timeout=15,
                    )
                    collect_mentions(name, result)
                    return result

                async def on_delta(delta):
                    nonlocal streamed_reply
                    if not delta:
                        return
                    streamed_text.append(str(delta))
                    preview = "".join(streamed_text).strip()[:2000]
                    if not preview:
                        return
                    if streamed_reply is None:
                        streamed_reply = await bounded(message.reply(preview))
                    else:
                        await bounded(streamed_reply.edit(content=preview))

                if getattr(llm, "api_key", "") and getattr(self.sidecar, "enabled", False):
                    try:
                        sidecar_response = await bounded(
                            self.sidecar.run(
                                messages,
                                tool_definitions,
                                execute_agent_tool,
                                on_delta=on_delta,
                            ),
                            timeout=90,
                        )
                    except Exception as error:
                        logger.info("Agent sidecar fallback message_id=%s error=%s", message.id, type(error).__name__)
                    if sidecar_response:
                        response = sidecar_response
                        await record_agent_event(
                            self.bot, event="agent_completed", model=response.model,
                            usage=response.usage,
                        )

                if response is None:
                    for tool_round in range(self.MAX_TOOL_ROUNDS + 1):
                        try:
                            response = await bounded(self._generate(
                                messages, tools=tool_definitions, max_tokens=max_tokens, temperature=profile.temperature
                            ))
                        except Exception as error:
                            logger.warning("AI generation failed message_id=%s error=%s", message.id, type(error).__name__)
                            await message.reply("Sorry, I had trouble talking to my brain right now.")
                            try:
                                owner = self.bot.get_user(Config.MASTER_ADMIN_ID) or await self.bot.fetch_user(Config.MASTER_ADMIN_ID)
                                await owner.send(f"⚠️ **AI Generation Failed** in {message.jump_url}\nError: `{type(error).__name__}: {str(error)}`")
                            except Exception as dm_err:
                                logger.error("Failed to DM owner about AI failure: %s", dm_err)
                            return

                        if not response.tool_calls:
                            break
                        if tool_round >= self.MAX_TOOL_ROUNDS:
                            messages.append({"role": "user", "content": "Tool limit reached. Answer using the verified context already available."})
                            response = await bounded(self._generate(messages, tools=None, temperature=profile.temperature))
                            break

                        if response.assistant_message:
                            messages.append(response.assistant_message)
                        else:
                            messages.append({
                                "role": "assistant",
                                "content": response.text or None,
                                "tool_calls": [
                                    {"id": call.call_id, "type": "function", "function": {
                                        "name": call.name, "arguments": json.dumps(call.arguments)
                                    }} for call in response.tool_calls
                                ],
                            })

                        for call in response.tool_calls:
                            tool_started = time.perf_counter()
                            result = await execute_agent_tool(call.name, call.arguments)
                            logger.info("AI stage message_id=%s stage=tool tool=%s duration_ms=%d",
                                        message.id, call.name, (time.perf_counter() - tool_started) * 1000)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call.call_id,
                                "content": json.dumps(result, ensure_ascii=False)[:2000]
                                if not isinstance(result, str) else result[:2000],
                            })

                reply_text = self._apply_tool_mentions(
                    (response.text if response else "").strip(), mention_sources
                )
                if not reply_text:
                    await message.reply("Sorry, I couldn't produce a reply this time.")
                    return

                pages = [reply_text[i:i + 2000] for i in range(0, len(reply_text), 2000)]
                for index, page in enumerate(pages):
                    if index == 0:
                        if streamed_reply is not None:
                            if page != "".join(streamed_text).strip()[:2000]:
                                await bounded(streamed_reply.edit(content=page))
                        else:
                            await bounded(message.reply(page))
                        self.context_builder.tracker.remember_exchange(message, user_text, reply_text)
                    else:
                        await bounded(message.channel.send(page))
                usage = response.usage if response else {}
                logger.info(
                    "AI stage message_id=%s stage=send model=%s total_ms=%d prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                    message.id,
                    response.model if response else "",
                    (time.perf_counter() - request_started) * 1000,
                    usage.get("prompt_tokens") if isinstance(usage, dict) else None,
                    usage.get("completion_tokens") if isinstance(usage, dict) else None,
                    usage.get("total_tokens") if isinstance(usage, dict) else None,
                )
                await record_agent_event(
                    self.bot, event="chat_completed", model=response.model if response else None,
                    usage=usage
                )
                if db and getattr(db, "user_quotas", None) is not None and usage.get("total_tokens"):
                    await db.increment_user_quota(message.author.id, usage["total_tokens"])
        finally:
            self._concurrency_limit.release()
