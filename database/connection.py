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
            
            # Create indexes for fast lookups. Each operation is isolated so a
            # duplicate-key or permissions error cannot prevent later indexes.
            async def ensure_index(label, operation):
                try:
                    await operation
                except Exception as error:
                    print(f"Index creation note ({label}): {error}")

            async def ensure_unique_index(collection, field_name):
                existing = await collection.index_information()
                idx_name = f"{field_name}_1"
                if idx_name in existing and not existing[idx_name].get("unique"):
                    await collection.drop_index(idx_name)
                await collection.create_index(field_name, unique=True)

            index_operations = [
                ("player_ranks.user_id", ensure_unique_index(self.player_ranks, "user_id")),
                ("tickets.id", ensure_unique_index(self.tickets, "id")),
                ("ranked_results.id", ensure_unique_index(self.ranked_results, "id")),
                ("ranked_results.ticket_id", ensure_unique_index(self.ranked_results, "ticket_id")),
                ("observation_results.id", ensure_unique_index(self.observation_results, "id")),
                ("observation_results.ticket_id", ensure_unique_index(self.observation_results, "ticket_id")),
                ("player_clips.user_id", ensure_unique_index(self.player_clips, "user_id")),
                ("web_users.discord_user_id", ensure_unique_index(self.web_users, "discord_user_id")),
                ("web_login_tokens.token_hash", ensure_unique_index(self.web_login_tokens, "token_hash")),
                ("web_sessions.session_hash", ensure_unique_index(self.web_sessions, "session_hash")),
                ("wallets.user_id", ensure_unique_index(self.wallets, "user_id")),
                ("bot_settings.key", self.bot_settings.create_index("key")),
                ("undo_logs.target_id_timestamp", self.undo_logs.create_index([("target_id", 1), ("timestamp", -1)])),
            ]

            async def ensure_challonge_match_index():
                existing = (await self.betting_matches.index_information()).get("challonge_match_id_1")
                if existing and not existing.get("partialFilterExpression"):
                    await self.betting_matches.drop_index("challonge_match_id_1")
                await self.betting_matches.create_index(
                    "challonge_match_id",
                    unique=True,
                    partialFilterExpression={"challonge_match_id": {"$exists": True}},
                )

            index_operations.extend([
                ("betting_matches.challonge_match_id", ensure_challonge_match_index()),
                ("betting_wagers.match_id_user_id", self.betting_wagers.create_index(
                    [("match_id", 1), ("user_id", 1)], unique=True,
                )),
                ("web_login_tokens.expires_at", self.web_login_tokens.create_index("expires_at", expireAfterSeconds=0)),
                ("web_sessions.expires_at", self.web_sessions.create_index("expires_at", expireAfterSeconds=0)),
                ("web_sessions.user_id_revoked_at", self.web_sessions.create_index([("user_id", 1), ("revoked_at", 1)])),
                ("wallet_transactions.user_id_created_at", self.wallet_transactions.create_index([("user_id", 1), ("created_at", -1)])),
                ("betting_matches.state_scheduled_at", self.betting_matches.create_index([("state", 1), ("scheduled_at", 1)])),
                ("betting_matches.source_phase_group", self.betting_matches.create_index([("source", 1), ("phase", 1), ("group", 1)])),
                ("betting_wagers.match_id_status", self.betting_wagers.create_index([("match_id", 1), ("status", 1)])),
                ("challonge_participants.tournament_participant", self.challonge_participants.create_index(
                    [("tournament_id", 1), ("challonge_participant_id", 1)], unique=True,
                )),
                ("betting_admin_audit.match_id_created_at", self.betting_admin_audit.create_index([("match_id", 1), ("created_at", -1)])),
                ("betting_notifications.delivery", self.betting_notifications.create_index([("status", 1), ("next_attempt_at", 1), ("created_at", 1)])),
                ("clip_review_notifications.delivery", self.clip_review_notifications.create_index([("status", 1), ("next_attempt_at", 1), ("created_at", 1)])),
                ("tickets.channel_id", self.tickets.create_index("channel_id")),
                ("tickets.status_type_user", self.tickets.create_index([("status", 1), ("ticket_type", 1), ("user_id", 1)])),
                ("tickets.status_type_closed_at", self.tickets.create_index([("status", 1), ("ticket_type", 1), ("closed_at", -1)])),
                ("tickets.user_history", self.tickets.create_index([("user_id", 1), ("status", 1), ("ticket_type", 1), ("closed_at", -1)])),
                ("tickets.opponent_history", self.tickets.create_index([("opponent_id", 1), ("status", 1), ("ticket_type", 1), ("closed_at", -1)])),
            ])
            for label, operation in index_operations:
                await ensure_index(label, operation)

            try:
                # Existing players predate movement tracking; initialize them
                # without overwriting a real delta from a previous mutation.
                await self.player_ranks.update_many(
                    {"rank_change": {"$exists": False}},
                    {"$set": {"rank_change": 0}},
                )
            except Exception as error:
                print(f"Player rank migration note: {error}")
            
            return True
        except Exception as e:
            print(f"MongoDB connection error: {e}")
            return False
    
