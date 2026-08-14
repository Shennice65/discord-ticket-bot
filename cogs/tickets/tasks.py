import discord
from discord.ext import commands, tasks
import asyncio
from datetime import datetime

from utils.ticket_utils import get_observer_mention


class TicketTasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.cleanup_stale_tickets.start()
        self.cleanup_pending_tickets.start()
        
    def cog_unload(self):
        self.cleanup_stale_tickets.cancel()
        self.cleanup_pending_tickets.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        print("TicketTasks cog loaded")

    @tasks.loop(hours=1)
    async def cleanup_stale_tickets(self):
        await self.bot.wait_until_ready()
        if self.db.tickets is None:
            return
            
        now_naive = datetime.utcnow()
        cursor = self.db.tickets.find({"status": "open", "ticket_type": "Ranked 1v1", "ducking_ping_sent": {"$ne": True}})
        open_tickets = await cursor.to_list(length=None)
        
        for ticket in open_tickets:
            channel = self.bot.get_channel(ticket['channel_id'])
            
            if channel:
                try:
                    try:
                        val = ticket['created_at']
                        created = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
                        if (now_naive - created).total_seconds() > 604800:
                            observer_mention = get_observer_mention(channel.guild)
                            await channel.send(f"{observer_mention} This ticket has been inactive for 7 days. Please check if the requested player is avoiding the match.")
                            await self.db.mark_ducking_ping_sent(ticket['channel_id'])
                    except (ValueError, TypeError):
                        pass
                except Exception as e:
                    print(f"Cleanup error on {channel.id}: {e}")
                    
    @tasks.loop(minutes=5)
    async def cleanup_pending_tickets(self):
        await self.bot.wait_until_ready()
        if self.db.tickets is None:
            return
            
        now_naive = datetime.utcnow()
        cursor = self.db.tickets.find({"status": "pending_accept"})
        pending_tickets = await cursor.to_list(length=None)
        
        for ticket in pending_tickets:
            try:
                val = ticket['created_at']
                created = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
                if (now_naive - created).total_seconds() > 86400:  # 24 hours
                    channel = self.bot.get_channel(ticket['channel_id'])
                    if channel:
                        try:
                            await channel.send("The out-of-range challenge has expired. This channel will be deleted in 10 seconds.")
                            await asyncio.sleep(10)
                            await channel.delete()
                        except discord.errors.NotFound:
                            pass
                    
                    # reset cooldown for requester
                    await self.db.reset_ranked_cooldown_only(ticket['user_id'])
                    # remove from DB
                    await self.db.tickets.delete_one({"_id": ticket['_id']})
            except (ValueError, TypeError, KeyError) as e:
                print(f"Pending cleanup error: {e}")


async def setup(bot):
    await bot.add_cog(TicketTasks(bot))
