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
        self.player_clips = None
        self.web_users = None
        self.web_login_tokens = None
        self.web_sessions = None
        self.wallets = None
        self.wallet_transactions = None
        self.betting_matches = None
        self.betting_wagers = None
        self.challonge_participants = None
        self.betting_admin_audit = None
        self.betting_notifications = None
        self.clip_review_notifications = None
        self.ladder_lock = asyncio.Lock()
    
    async def init(self):
        """Connect to MongoDB"""
        try:
            if not self.uri:
                print("MONGO_URI not found in config! Please set it in .env.")
                return False
                
            import certifi
            self.client = AsyncIOMotorClient(self.uri, tlsCAFile=certifi.where())
            self.db = self.client[Config.MONGO_DB_NAME]
            
            self.tickets = self.db.tickets
            self.ranked_results = self.db.ranked_results
            self.observation_results = self.db.observation_results
            self.player_ranks = self.db.player_ranks
            self.undo_logs = self.db.undo_logs
            self.bot_settings = self.db.bot_settings
            self.bot_config = self.db.bot_config
            self.player_clips = self.db.player_clips
            self.web_users = self.db.web_users
            self.web_login_tokens = self.db.web_login_tokens
            self.web_sessions = self.db.web_sessions
            self.wallets = self.db.wallets
            self.wallet_transactions = self.db.wallet_transactions
            self.betting_matches = self.db.betting_matches
            self.betting_wagers = self.db.betting_wagers
            self.challonge_participants = self.db.challonge_participants
            self.betting_admin_audit = self.db.betting_admin_audit
            self.betting_notifications = self.db.betting_notifications
            self.clip_review_notifications = self.db.clip_review_notifications
            
            # Simple ping to test connection
            await self.db.command('ping')
            print("Connected to MongoDB successfully!")
            
            # Create indexes for fast lookups
            try:
                async def ensure_unique_index(collection, field_name):
                    existing = await collection.index_information()
                    idx_name = f"{field_name}_1"
                    if idx_name in existing and not existing[idx_name].get("unique"):
                        await collection.drop_index(idx_name)
                    await collection.create_index(field_name, unique=True)
                
                await ensure_unique_index(self.player_ranks, "user_id")
                await ensure_unique_index(self.tickets, "id")
                await ensure_unique_index(self.ranked_results, "id")
                await ensure_unique_index(self.ranked_results, "ticket_id")
                await ensure_unique_index(self.observation_results, "id")
                await ensure_unique_index(self.observation_results, "ticket_id")
                await ensure_unique_index(self.db.player_clips, "user_id")
                await ensure_unique_index(self.web_users, "discord_user_id")
                await ensure_unique_index(self.web_login_tokens, "token_hash")
                await ensure_unique_index(self.web_sessions, "session_hash")
                await ensure_unique_index(self.wallets, "user_id")
                challonge_index = (await self.betting_matches.index_information()).get("challonge_match_id_1")
                if challonge_index and not challonge_index.get("partialFilterExpression"):
                    await self.betting_matches.drop_index("challonge_match_id_1")
                await self.betting_matches.create_index(
                    "challonge_match_id",
                    unique=True,
                    partialFilterExpression={"challonge_match_id": {"$exists": True}},
                )
                await self.betting_wagers.create_index(
                    [("match_id", 1), ("user_id", 1)],
                    unique=True,
                )
                await self.web_login_tokens.create_index("expires_at", expireAfterSeconds=0)
                await self.web_sessions.create_index("expires_at", expireAfterSeconds=0)
                await self.web_sessions.create_index([("user_id", 1), ("revoked_at", 1)])
                await self.wallet_transactions.create_index([("user_id", 1), ("created_at", -1)])
                await self.betting_matches.create_index([("state", 1), ("scheduled_at", 1)])
                await self.betting_matches.create_index([("source", 1), ("phase", 1), ("group", 1)])
                await self.betting_wagers.create_index([("match_id", 1), ("status", 1)])
                await self.challonge_participants.create_index(
                    [("tournament_id", 1), ("challonge_participant_id", 1)],
                    unique=True,
                )
                await self.betting_admin_audit.create_index([("match_id", 1), ("created_at", -1)])
                await self.betting_notifications.create_index([("status", 1), ("next_attempt_at", 1), ("created_at", 1)])
                await self.clip_review_notifications.create_index([("status", 1), ("next_attempt_at", 1), ("created_at", 1)])
                
                await self.tickets.create_index("channel_id")
                await self.tickets.create_index([("status", 1), ("ticket_type", 1), ("user_id", 1)])
                await self.tickets.create_index([("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                # New indexes for rapid history command execution
                await self.tickets.create_index([("user_id", 1), ("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                await self.tickets.create_index([("opponent_id", 1), ("status", 1), ("ticket_type", 1), ("closed_at", -1)])
                # Existing players predate movement tracking; initialize them
                # without overwriting a real delta from a previous mutation.
                await self.player_ranks.update_many(
                    {"rank_change": {"$exists": False}},
                    {"$set": {"rank_change": 0}},
                )
            except Exception as e:
                print(f"Index creation note: {e}")
            
            return True
        except Exception as e:
            print(f"MongoDB connection error: {e}")
            return False
    
