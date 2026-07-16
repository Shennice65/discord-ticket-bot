import asyncio
import os
import discord
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import certifi
import re

load_dotenv()

class TransformBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True # Needed to fetch member display_name
        super().__init__(intents=intents)
        
    async def setup_hook(self):
        uri = os.environ.get('MONGO_URI') or os.getenv('MONGO_URI')
        self.db = AsyncIOMotorClient(uri, tlsCAFile=certifi.where()).discord_bot_db

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        print("Transforming old Ticket Created embeds...")
        
        cursor = self.db.tickets.find({"status": "open"})
        open_tickets = await cursor.to_list(length=None)
        
        updated = 0
        for t in open_tickets:
            channel_id = t.get('channel_id')
            try:
                channel = await self.fetch_channel(channel_id)
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                    
                # Look for the ticket created message
                async for message in channel.history(limit=20, old_first=True):
                    if message.author.id == self.user.id and message.embeds:
                        old_embed = message.embeds[0]
                        if old_embed.title and old_embed.title.startswith("Ticket Created"):
                            # Found the embed, let's transform it
                            has_user_id = any(f.name == "User ID" for f in old_embed.fields)
                            needs_stats_update = any(f.name.endswith("'s Stats") and not f.value.startswith("```") for f in old_embed.fields)
                            
                            # Mention check
                            needs_unmention = False
                            for f in old_embed.fields:
                                if f.name == "Created By" and re.match(r'<@!?(\d+)>', f.value):
                                    needs_unmention = True
                                    break
                                    
                            if not has_user_id and not needs_stats_update and not needs_unmention:
                                # Already updated
                                break
                                
                            new_embed = discord.Embed(
                                title=old_embed.title,
                                description=old_embed.description,
                                color=old_embed.color,
                                timestamp=old_embed.timestamp
                            )
                            if old_embed.thumbnail:
                                new_embed.set_thumbnail(url=old_embed.thumbnail.url)
                            if old_embed.footer:
                                new_embed.set_footer(text=old_embed.footer.text, icon_url=old_embed.footer.icon_url)

                            for field in old_embed.fields:
                                if field.name == "User ID":
                                    continue
                                
                                value = field.value
                                if field.name == "Created By":
                                    # Convert mention to display name
                                    match = re.match(r'<@!?(\d+)>', value)
                                    if match:
                                        user_id = int(match.group(1))
                                        member = channel.guild.get_member(user_id)
                                        if member:
                                            value = member.display_name
                                        else:
                                            try:
                                                user = await self.fetch_user(user_id)
                                                value = user.name
                                            except:
                                                pass
                                elif field.name.endswith("'s Stats"):
                                    if not value.startswith("```"):
                                        value = f"```\n{value}\n```"
                                        
                                new_embed.add_field(name=field.name, value=value, inline=field.inline)
                                
                            await message.edit(embed=new_embed)
                            print(f"Updated embed in #{channel.name}")
                            updated += 1
                            break # Move to next ticket
                            
            except discord.NotFound:
                pass
            except discord.Forbidden:
                pass
            except Exception as e:
                print(f"Error checking {channel_id}: {e}")
                
        print(f"\nDone! Transformed {updated} ticket embeds.")
        await self.close()

if __name__ == "__main__":
    bot = TransformBot()
    bot.run(os.environ.get('DISCORD_TOKEN'))
