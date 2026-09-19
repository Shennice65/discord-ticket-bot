import discord
from discord.ext import commands
from google import genai
from google.genai import types
from config import Config
from collections import defaultdict, deque
import math
from datetime import datetime, timezone

def cosine_similarity(v1, v2):
    dot_product = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0:
        return 0
    return dot_product / (mag1 * mag2)

class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = Config.GEMINI_API_KEY
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        
        # Store recent history per channel. Limit to last 15 messages to save tokens.
        self.history = defaultdict(lambda: deque(maxlen=15))
        
        # System instructions to give the bot a persona
        self.system_instruction = (
            "You are a member of a Discord community. Text exactly like an actual user in a casual chat. "
            "Use short words, abbreviations, and slangs (like fr, tbh, ngl, lol, lmao). "
            "Do NOT use periods at the end of your sentences in most cases. Use fewer commas and keep capitalization natural (often lowercase). "
            "Keep it very brief, natural, and chill. Feel free to use community inside jokes if relevant. "
            "Do NOT sound like an AI assistant or professional customer service."
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore messages from bots
        if message.author.bot:
            return
            
        # Check if the bot is mentioned
        if self.bot.user not in message.mentions:
            return
            
        # Restrict access to administrators only
        if not getattr(message.author, 'guild_permissions', None) or not message.author.guild_permissions.administrator:
            await message.reply("Sorry, currently only server administrators can chat with me.")
            return

            
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
                            model='text-embedding-004',
                            contents=user_text
                        )
                        if hasattr(emb_response, 'embeddings') and emb_response.embeddings:
                            # Convert to a standard Python list so MongoDB can serialize it
                            query_embedding = list(emb_response.embeddings[0].values)
                    except Exception as e:
                        print(f"Embedding error: {e}")
                        
                # 2. Retrieve relevant context from database
                recalled_context = ""
                if query_embedding and getattr(self.bot, 'db', None) and getattr(self.bot.db, 'chat_memory', None) is not None:
                    # Fetch last 100 messages to search in memory (reduced from 500 for lightning speed)
                    cursor = self.bot.db.chat_memory.find({"channel_id": message.channel.id}).sort("timestamp", -1).limit(100)
                    past_exchanges = await cursor.to_list(length=100)
                    
                    scored_exchanges = []
                    for exchange in past_exchanges:
                        emb = exchange.get("embedding")
                        if emb:
                            score = cosine_similarity(query_embedding, emb)
                            scored_exchanges.append((score, exchange))
                    
                    # Sort by score descending and take top 3
                    scored_exchanges.sort(key=lambda x: x[0], reverse=True)
                    top_exchanges = scored_exchanges[:3]
                    
                    if top_exchanges:
                        recalled_context = "### RECALLED LONG-TERM CONTEXT ###\nThe following are relevant past conversations with this user/channel:\n"
                        for score, ex in top_exchanges:
                            recalled_context += f"- User said: {ex.get('user_text')}\n- You replied: {ex.get('bot_reply')}\n\n"
                            
                # Prepare contents for Gemini
                contents = []
                # Append history
                for hist_msg in self.history[message.channel.id]:
                    contents.append(hist_msg)
                
                # Append current message
                parts = []
                if user_text:
                    parts.append(types.Part.from_text(text=user_text))
                    
                # Process any image attachments
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        image_bytes = await attachment.read()
                        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=attachment.content_type))
                        
                if not parts:
                    return

                current_content = types.Content(
                    role="user",
                    parts=parts
                )
                contents.append(current_content)
                
                # Add recalled context to system instructions
                dynamic_system_instruction = self.system_instruction
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
                
                reply_text = response.text
                
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
                    await self.bot.db.chat_memory.insert_one({
                        "channel_id": message.channel.id,
                        "user_id": message.author.id,
                        "user_text": user_text,
                        "bot_reply": reply_text,
                        "embedding": query_embedding,
                        "timestamp": datetime.now(timezone.utc)
                    })
                
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

async def setup(bot):
    await bot.add_cog(Chat(bot))
