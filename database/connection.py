import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
import asyncio


class ConnectionMixin:
    def __init__(self):
        self.uri = Config.MONGO_URI
        self.client = None
        self.db = None
        self.tickets = None
        self.ranked_results = None
        self.observation_results = None
        self.player_ranks = None
        self.undo_logs = None
        self.bot_settings = None
        self.bot_config = None
        self.ladder_lock = asyncio.Lock()
    
    async def init(self):
        """Connect to MongoDB"""
        try:
            if not self.uri:
                print("MONGO_URI not found in config! Please set it in .env.")
                return False
                
            import certifi
            self.client = AsyncIOMotorClient(self.uri, tlsCAFile=certifi.where())
            self.db = self.client.discord_bot_db
            
            self.tickets = self.db.tickets
            self.ranked_results = self.db.ranked_results
            self.observation_results = self.db.observation_results
            self.player_ranks = self.db.player_ranks
            self.undo_logs = self.db.undo_logs
            self.bot_settings = self.db.bot_settings
            self.bot_config = self.db.bot_config
            
            # Simple ping to test connection
            await self.db.command('ping')
            print("Connected to MongoDB successfully!")
            
            # Create indexes for fast lookups
            try:
                # Drop old non-unique index if it exists before creating unique one
                existing = await self.player_ranks.index_information()
                if "user_id_1" in existing and not existing["user_id_1"].get("unique"):
                    await self.player_ranks.drop_index("user_id_1")
                await self.player_ranks.create_index("user_id", unique=True)
                await self.tickets.create_index("channel_id")
                await self.tickets.create_index([("status", 1), ("ticket_type", 1), ("user_id", 1)])
                await self.tickets.create_index([("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                # New indexes for rapid history command execution
                await self.tickets.create_index([("user_id", 1), ("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                await self.tickets.create_index([("opponent_id", 1), ("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                
                await self.ranked_results.create_index("ticket_id")
                await self.observation_results.create_index("ticket_id")
            except Exception as e:
                print(f"Index creation note: {e}")
            
            return True
        except Exception as e:
            print(f"MongoDB connection error: {e}")
            return False
    
