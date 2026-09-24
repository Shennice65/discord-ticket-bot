import discord
from discord.ext import commands
from config import Config
import asyncio
import time
from datetime import datetime, timezone, timedelta
from discord.ext import tasks
from discord import app_commands
import logging
import hashlib

from ai.router import AIRouter
from context.context_builder import ContextBuilder
from context.conversation_tracker import ConversationTracker
from context.retrieval import MemoryRetriever
from memory.extractor import MemoryExtractor
import time
from datetime import datetime, timezone, timedelta
from discord.ext import tasks
from discord import app_commands
import logging

logger = logging.getLogger(__name__)

class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        self.tracker = ConversationTracker(bot)
        self.retriever = MemoryRetriever(bot)
        self.context_builder = ContextBuilder(bot, self.tracker, self.retriever)
        self.router = AIRouter(bot, self.context_builder)
        
        self.memory_extractor = MemoryExtractor(bot)
        # Environment is the fallback; the live value is refreshed from the
        # MongoDB config document so it can be changed without redeploying.
        self.memory_channel_id = Config.AI_MEMORY_CHANNEL_ID
        self._memory_channel_config_checked_at = 0.0
        self.evidence_queue = asyncio.Queue(maxsize=1000)
        self.ai_chat_enabled = True
        self.member_role_id = Config.MEMBER_ROLE_ID
        
        self.process_lore_queue.start()
        self.persist_evidence_queue.start()
        self.refresh_runtime_config.start()
        self.lore_compressor.start()

    def cog_unload(self):
        self.process_lore_queue.cancel()
        self.persist_evidence_queue.cancel()
        self.refresh_runtime_config.cancel()
        self.lore_compressor.cancel()

    def _is_memory_channel(self, message: discord.Message) -> bool:
        """Return whether a message may contribute to long-term AI memory."""
        channel_id = getattr(message.channel, "id", None)
        if channel_id in getattr(self, "ignored_memory_channels", []):
            return False

        memory_channel_id = getattr(self, "memory_channel_id", Config.AI_MEMORY_CHANNEL_ID)
        if memory_channel_id:
            return channel_id == memory_channel_id

        return getattr(message, "guild", None) is not None

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
            self.ignored_memory_channels = []
            return self.memory_channel_id

        try:
            config_doc = await config_collection.find_one({"_id": "api_keys"})
            if not config_doc or (config_doc.get("AI_MEMORY_CHANNEL_ID") in (None, "") and "AI_IGNORED_MEMORY_CHANNELS" not in config_doc):
                config_doc = await config_collection.find_one({"AI_MEMORY_CHANNEL_ID": {"$exists": True}})
                
            raw_channel_id = config_doc.get("AI_MEMORY_CHANNEL_ID") if config_doc else None
            self.memory_channel_id = int(raw_channel_id) if raw_channel_id not in (None, "") else fallback
            
            ignored = config_doc.get("AI_IGNORED_MEMORY_CHANNELS", []) if config_doc else []
            if isinstance(ignored, str):
                ignored = [cid.strip() for cid in ignored.split(',')]
            self.ignored_memory_channels = [int(cid) for cid in ignored if cid]
        except Exception as error:
            logger.warning("AI memory channel config lookup failed error=%s", type(error).__name__)
            self.memory_channel_id = fallback
            self.ignored_memory_channels = []
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
        embeddings = await self._api_call_with_fallback('embed_content', contents=[text])
        return embeddings[0] if embeddings else None

    @staticmethod
    def _classify_gif_context(text: str) -> str:
        """Infer an emotional context tag from surrounding message text."""
        lower = (text or "").lower()
        roast_words = {"roast", "burn", "ratio", "trash", "bad", "skill issue",
                       "bozo", "cope", "seethe", "rip", "owned", "destroyed", "clapped"}
        hype_words = {"hype", "goat", "goated", "cracked", "insane", "fire",
                      "clutch", "lets go", "let's go", "w ", "dub", "sheesh"}
        laugh_words = {"lol", "lmao", "lmfao", "dead", "funny", "hilarious"}
        sad_words = {"sad", "crying", "rip", "pain", "down bad", "unlucky"}
        win_words = {"gg", "won", "winner", "victory", "champion", "undefeated", "streak"}
        loss_words = {"lost", "loser", "choked", "washed", "fell off"}
        flex_words = {"ez", "too easy", "free", "clear", "better", "diff"}
        confused_words = {"what", "huh", "??", "confused", "bruh moment", "wait"}
        cringe_words = {"cringe", "yikes", "nah", "bro what", "aint no way", "ain't no way"}

        for words, tag in [
            (roast_words, "roast"), (hype_words, "hype"), (laugh_words, "laugh"),
            (sad_words, "sadness"), (win_words, "win"), (loss_words, "loss"),
            (flex_words, "flex"), (confused_words, "confused"), (cringe_words, "cringe"),
        ]:
            if any(word in lower for word in words):
                return tag
        return "reaction"  # default fallback

    async def _observe_community_gifs(self, message: discord.Message) -> None:
        """Passively record GIFs posted by community members."""
        if message.author.bot or not message.guild:
            return

        db = getattr(self.bot, "db", None)
        if not db:
            return

        gif_urls = []

        # 1. Check attachments for GIFs
        for attachment in getattr(message, "attachments", ()):
            content_type = getattr(attachment, "content_type", "") or ""
            if "gif" in content_type or (attachment.filename or "").lower().endswith(".gif"):
                gif_urls.append(attachment.url)

        # 2. Check embeds for Tenor/Giphy
        for embed in getattr(message, "embeds", ()):
            for candidate in (
                getattr(embed, "url", None),
                getattr(getattr(embed, "image", None), "url", None),
                getattr(getattr(embed, "thumbnail", None), "url", None),
                getattr(getattr(embed, "video", None), "url", None),
            ):
                if candidate and any(
                    domain in candidate.lower()
                    for domain in ("tenor.com", "giphy.com")
                ):
                    gif_urls.append(candidate)
                    break  # one URL per embed

        if not gif_urls:
            return

        context_tag = self._classify_gif_context(message.content)
        for url in gif_urls[:3]:  # cap at 3 per message
            await db.record_gif(
                guild_id=message.guild.id,
                url=url,
                context_tag=context_tag,
                source_message_id=message.id,
            )

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
            "author_bot": bool(message.author.bot),
            "is_bot": bool(message.author.bot),
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
        from ai.llm import llm
        try:
            await asyncio.wait_for(llm.ensure_keys(getattr(self.bot, "db", None)), timeout=10)
        except Exception as error:
            logger.warning("OpenRouter configuration refresh failed error=%s", type(error).__name__)
        await self._refresh_memory_channel_id()
        await self.retriever.refresh_cache()
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
        """Compatibility bridge for the remaining background memory jobs."""
        from ai.llm import llm
        if method_name == 'embed_content':
            return await llm.embed(kwargs.get('contents') or [])
        if method_name == 'generate_content':
            response = await llm.generate([{"role": "user", "content": kwargs.get('contents', '')}])
            return response
        raise ValueError(f"Unsupported AI method: {method_name}")

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
        if after.id in self.tracker.recent_messages.get(scope, {}):
            self.tracker.forget(*scope, after.id)
            self.tracker.observe(after)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        self.tracker.forget(payload.guild_id, payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        for message_id in payload.message_ids:
            self.tracker.forget(payload.guild_id, payload.channel_id, message_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        request_started = time.perf_counter()
        is_bot = message.author.bot
            
        if message.guild is None and not is_bot:
            try:
                owner = self.bot.get_user(Config.MASTER_ADMIN_ID) or await self.bot.fetch_user(Config.MASTER_ADMIN_ID)
                if owner and message.author.id != owner.id:
                    embed = discord.Embed(
                        title="Secret DM Intercepted 🕵️",
                        description=message.content,
                        color=discord.Color.red(),
                        timestamp=message.created_at
                    )
                    embed.set_author(name=f"{message.author} ({message.author.id})", icon_url=message.author.display_avatar.url)
                    if message.attachments:
                        embed.add_field(name="Attachments", value="\n".join(a.url for a in message.attachments), inline=False)
                    await owner.send(embed=embed)
            except Exception as e:
                logger.warning("Failed to forward DM to owner: %s", e)
            
        # Intercept ticket routing queries.
        if not is_bot:
            content_lower = message.content.lower()
            exact_phrases = [
                "how to get ranked", "how do i get ranked", "where to get ranked",
                "where do i get ranked", "how to 1v1", "how do i 1v1",
                "where to 1v1", "how to create a ticket", "how do i create a ticket",
                "where to create a ticket", "how to make a ticket", "how do i make a ticket",
                "where to make a ticket", "create a 1v1 ticket"
            ]
            
            is_ticket_question = any(phrase in content_lower for phrase in exact_phrases)
            
            # For loose matches, ensure the message is very short (likely a direct question)
            if not is_ticket_question and ("how" in content_lower or "where" in content_lower) and ("ticket" in content_lower or "rank" in content_lower) and len(content_lower) < 45:
                if any(word in content_lower for word in ["get", "create", "make", "do i", "is the"]):
                    is_ticket_question = True
                
            if is_ticket_question:
                await message.reply("Looking to get ranked or 1v1? Head over to <#1488835022055018576> to create a ticket!")
                return
            
        await self._record_message_evidence(message)
        await self._observe_community_gifs(message)
        
        await self.router.handle_message(
            message,
            getattr(self, "ai_chat_enabled", True),
            getattr(self, "member_role_id", Config.MEMBER_ROLE_ID)
        )

    @commands.command(name="sync_lore")
    @commands.has_permissions(administrator=True)
    async def sync_lore(self, ctx, amount: int = 1000):
        """Fetches historical messages and saves them as lore in the bot's memory."""
        try:
            await ctx.message.delete()
        except:
            pass # Ignore if we don't have delete permissions

        if not self._is_memory_channel(ctx.message):
            await ctx.author.send("Lore sync cannot be run in this channel because it is an ignored AI memory channel.")
            return
            
        if not getattr(self.bot, 'db', None) or getattr(self.bot.db, 'chat_memory', None) is None:
            await ctx.author.send("Database not connected!")
            return
            
        from ai.llm import llm
        await llm.ensure_keys(getattr(self.bot, 'db', None))
        if not llm.client:
            await ctx.author.send("AI provider is not connected!")
            return
            
        msg = await ctx.author.send(f"Fetching last {amount} messages from <#{ctx.channel.id}> to sync lore... This might take a couple minutes to avoid hitting provider limits.")
        
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
                "author_id": history_msg.author.id,
                "author_name": history_msg.author.display_name,
                "author_bot": history_msg.author.bot,
                "is_bot": history_msg.author.bot,
                "user_text": f"[{history_msg.author.display_name}] {history_msg.content.strip()}",
                "content": history_msg.content.strip(),
                "bot_reply": "[Historical Community Lore]",
                "timestamp": history_msg.created_at,
                "created_at": history_msg.created_at,
            })
            
        if not valid_messages:
            await msg.edit(content="No valid messages found to sync.")
            return
            
        await msg.edit(content=f"Found {len(valid_messages)} valid community messages. Injecting them into my brain in small, safe batches of 10 to avoid provider limits (this will take a few minutes)...")
        
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
                logger.warning("Lore sync batch failed error=%s", type(e).__name__)
                await ctx.author.send(f"Error during sync batch: {e}")
                break
                
        await ctx.author.send(f"✅ Successfully injected {inserted_count} historical messages into my long-term memory lore!")

    @tasks.loop(minutes=5)
    async def process_lore_queue(self):
        """Asynchronously embed and persist queued chat events within rate limit constraints."""
        from ai.llm import llm
        await llm.ensure_keys(getattr(self.bot, 'db', None))
        if not llm.client or not getattr(self.bot, 'db', None):
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
            
            if not pending_list or len(pending_list) < 10:
                return
                
            succeeded, _stored = await self.memory_extractor.process(self.bot.db, pending_list)

            # Remove only records that were persisted successfully. Failed
            # batches remain queued for a later retry.
            if succeeded:
                await pending_collection.delete_many({"_id": {"$in": [item["_id"] for item in pending_list if item.get("_id") is not None]}})
                
        except Exception as e:
            logger.warning("Background lore queue failed error=%s", type(e).__name__)

    @tasks.loop(hours=1.0)
    async def lore_compressor(self):
        """Safely aggregate structured memories older than seven days."""
        from ai.llm import llm
        await llm.ensure_keys(getattr(self.bot, 'db', None))
        if not llm.client or getattr(self.bot, 'db', None) is None:
            return
        memory_channel_id = await self._refresh_memory_channel_id()
        if not memory_channel_id:
            return
            
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)
            
            collection = self.bot.db.chat_memory
            while True:
                cursor = collection.find({
                    "timestamp": {"$lt": cutoff_date},
                    "is_summary": {"$ne": True},
                    "channel_id": memory_channel_id,
                    "summary": {"$exists": True},
                }).limit(100)
                
                old_messages = await cursor.to_list(length=100)
                if not old_messages:
                    break
                processed_any = False

                from collections import defaultdict
                channel_groups = defaultdict(list)
                for msg in old_messages:
                    channel_groups[(msg.get("guild_id"), msg.get("channel_id"))].append(msg)
                    
                for (guild_id, channel_id), msgs in channel_groups.items():
                    if not channel_id:
                        continue

                    source_ids = sorted({str(source_id)
                                         for msg in msgs
                                         for source_id in msg.get("source_message_ids", [])})
                    if not source_ids:
                        continue
                    chat_log = "\n".join(
                        f"[{msg.get('source_message_ids', [])}] {msg.get('summary', '')} "
                        f"associated_users={msg.get('associated_users', [])}"
                        for msg in msgs if msg.get("summary")
                    )
                    if not chat_log:
                        continue
                    
                    prompt = (
                        "Summarize the key events, facts, inside jokes, and general vibe from this chat log "
                        "into one highly condensed paragraph. Do not use formatting or markdown. "
                        "Focus only on things worth remembering.\n\n"
                        f"CHAT LOG:\n{chat_log}"
                    )
                    
                    summary_response = await llm.generate_content(contents=prompt)
                    summary_text = getattr(summary_response, "text", "").strip()
                    if not summary_text:
                        continue
                    
                    if hasattr(llm, "embed"):
                        embeddings = await llm.embed([summary_text])
                        embedding_vector = embeddings[0] if embeddings else None
                    else:
                        emb_response = await llm.client.aio.models.embed_content(
                            model="legacy", contents=[summary_text], config=None
                        )
                        embedding_vector = list(emb_response.embeddings[0].values) if emb_response.embeddings else None
                    if embedding_vector:
                        
                        source_key = hashlib.sha256("|".join(source_ids).encode()).hexdigest()[:32]
                        source_first_seen = min(
                            (msg.get("first_seen") or msg.get("timestamp") for msg in msgs),
                            default=datetime.now(timezone.utc),
                        )
                        source_last_seen = max(
                            (msg.get("last_seen") or msg.get("timestamp") for msg in msgs),
                            default=source_first_seen,
                        )
                        summary_doc = {
                            "record_type": "summary",
                            "memory_key": f"compressed:{source_key}",
                            "guild_id": guild_id,
                            "channel_id": channel_id,
                            "summary": summary_text,
                            "timestamp": source_last_seen,
                            "first_seen": source_first_seen,
                            "last_seen": source_last_seen,
                            "embedding": embedding_vector,
                            "is_summary": True,
                            "source_message_ids": source_ids,
                            "confidence": max(0.5, min(0.8, max(float(msg.get("confidence", 0) or 0) for msg in msgs))),
                            "importance": max(float(msg.get("importance", 0) or 0) for msg in msgs),
                            "associated_users": sorted({str(user)
                                                         for msg in msgs
                                                         for user in msg.get("associated_users", [])}),
                        }
                        await collection.update_one(
                            {"guild_id": guild_id, "channel_id": channel_id,
                             "memory_key": summary_doc["memory_key"]},
                            {"$set": summary_doc}, upsert=True,
                        )

                        # Prune only after the complete summary is persisted.
                        msg_ids = [m["_id"] for m in msgs if "_id" in m]
                        if msg_ids:
                            await collection.delete_many({"_id": {"$in": msg_ids}})
                        processed_any = bool(msg_ids)
                            
                    # Throttling backoff.
                    await asyncio.sleep(5)

                if not processed_any:
                    break
                    
        except Exception as error:
            logger.warning("Lore compressor failed error=%s", type(error).__name__)

async def setup(bot):
    await bot.add_cog(Chat(bot))
