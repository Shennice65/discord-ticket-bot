import asyncpg
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config

class Database:
    def __init__(self):
        self.pool = None
        self.database_url = Config.DATABASE_URL
    
    async def init(self):
        """Initialize database tables and connection pool"""
        if not self.database_url:
            print("Warning: No DATABASE_URL set")
            return
        
        try:
            # Create SSL context for Supabase
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            self.pool = await asyncpg.create_pool(
                self.database_url,
                ssl=ssl_context,
                min_size=1,
                max_size=5
            )
            
            async with self.pool.acquire() as conn:
                # Create tickets table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS tickets (
                        id SERIAL PRIMARY KEY,
                        channel_id BIGINT UNIQUE,
                        user_id BIGINT,
                        ticket_type TEXT,
                        status TEXT DEFAULT 'open',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        closed_at TIMESTAMP,
                        closed_by BIGINT,
                        opponent TEXT,
                        private_link TEXT
                    )
                ''')
                
                # Create ranked_results table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS ranked_results (
                        id SERIAL PRIMARY KEY,
                        ticket_id INTEGER REFERENCES tickets(id),
                        observer_id BIGINT,
                        observer_name TEXT,
                        starting_rank TEXT,
                        ending_rank TEXT,
                        winner TEXT,
                        note TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create observation_results table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS observation_results (
                        id SERIAL PRIMARY KEY,
                        ticket_id INTEGER REFERENCES tickets(id),
                        observer_id BIGINT,
                        observer_name TEXT,
                        starting_rank TEXT,
                        ending_rank TEXT,
                        note TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                print("Database connected and tables ready")
        except Exception as e:
            print(f"Database connection error: {e}")
            self.pool = None
    
    async def create_ticket(self, channel_id: int, user_id: int, ticket_type: str, 
                           opponent: Optional[str] = None, private_link: Optional[str] = None) -> int:
        if not self.pool:
            print("No database connection")
            return 0
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                INSERT INTO tickets (channel_id, user_id, ticket_type, opponent, private_link)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            ''', channel_id, user_id, ticket_type, opponent, private_link)
            return row['id']
    
    async def close_ticket(self, channel_id: int, closed_by: int):
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE tickets 
                SET status = 'closed', closed_at = CURRENT_TIMESTAMP, closed_by = $1
                WHERE channel_id = $2
            ''', closed_by, channel_id)
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        if not self.pool:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM tickets WHERE channel_id = $1
            ''', channel_id)
            return dict(row) if row else None
    
    async def add_ranked_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                starting_rank: str, ending_rank: str, winner: str, note: Optional[str] = None):
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO ranked_results (ticket_id, observer_id, observer_name, starting_rank, ending_rank, winner, note)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            ''', ticket_id, observer_id, observer_name, starting_rank, ending_rank, winner, note)
    
    async def add_observation_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                     starting_rank: str, ending_rank: str, note: Optional[str] = None):
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO observation_results (ticket_id, observer_id, observer_name, starting_rank, ending_rank, note)
                VALUES ($1, $2, $3, $4, $5, $6)
            ''', ticket_id, observer_id, observer_name, starting_rank, ending_rank, note)
    
    async def get_user_history(self, user_id: int, limit: int = 10) -> Dict[str, List]:
        if not self.pool:
            return {'ranked': [], 'observations': []}
        async with self.pool.acquire() as conn:
            ranked_rows = await conn.fetch('''
                SELECT t.*, r.observer_name, r.starting_rank, r.ending_rank, r.winner, r.note, r.created_at as result_date
                FROM tickets t
                JOIN ranked_results r ON t.id = r.ticket_id
                WHERE t.user_id = $1 AND t.status = 'closed'
                ORDER BY t.closed_at DESC
                LIMIT $2
            ''', user_id, limit)
            
            obs_rows = await conn.fetch('''
                SELECT t.*, o.observer_name, o.starting_rank, o.ending_rank, o.note, o.created_at as result_date
                FROM tickets t
                JOIN observation_results o ON t.id = o.ticket_id
                WHERE t.user_id = $1 AND t.status = 'closed'
                ORDER BY t.closed_at DESC
                LIMIT $2
            ''', user_id, limit)
            
            return {
                'ranked': [dict(row) for row in ranked_rows],
                'observations': [dict(row) for row in obs_rows]
            }
    
    async def clear_ranked_history(self, user_id: int) -> int:
        if not self.pool:
            return 0
        async with self.pool.acquire() as conn:
            ticket_ids = await conn.fetch('''
                SELECT id FROM tickets 
                WHERE user_id = $1 AND ticket_type = 'Ranked 1v1'
            ''', user_id)
            
            ids = [row['id'] for row in ticket_ids]
            if not ids:
                return 0
            
            await conn.execute('''
                DELETE FROM ranked_results 
                WHERE ticket_id = ANY($1)
            ''', ids)
            
            await conn.execute('''
                DELETE FROM tickets 
                WHERE id = ANY($1)
            ''', ids)
            
            return len(ids)
    
    async def clear_observation_history(self, user_id: int) -> int:
        if not self.pool:
            return 0
        async with self.pool.acquire() as conn:
            ticket_ids = await conn.fetch('''
                SELECT id FROM tickets 
                WHERE user_id = $1 AND ticket_type = 'Personal Observation'
            ''', user_id)
            
            ids = [row['id'] for row in ticket_ids]
            if not ids:
                return 0
            
            await conn.execute('''
                DELETE FROM observation_results 
                WHERE ticket_id = ANY($1)
            ''', ids)
            
            await conn.execute('''
                DELETE FROM tickets 
                WHERE id = ANY($1)
            ''', ids)
            
            return len(ids)
    
    async def clear_all_history(self, user_id: int) -> tuple:
        ranked = await self.clear_ranked_history(user_id)
        obs = await self.clear_observation_history(user_id)
        return (ranked, obs)