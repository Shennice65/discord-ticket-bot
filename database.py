import libsql_experimental as libsql
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config

class Database:
    def __init__(self):
        self.client = None
        self.url = Config.TURSO_DATABASE_URL
        self.token = Config.TURSO_AUTH_TOKEN
    
    async def init(self):
        """Initialize database tables"""
        if not self.url or not self.token:
            print("Warning: Turso credentials not set")
            return
        
        try:
            self.client = libsql.connect(
                database=self.url,
                auth_token=self.token
            )
            
            self.client.execute('''
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER UNIQUE,
                    user_id INTEGER,
                    ticket_type TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP,
                    closed_by INTEGER,
                    opponent TEXT,
                    private_link TEXT
                )
            ''')
            
            self.client.execute('''
                CREATE TABLE IF NOT EXISTS ranked_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER REFERENCES tickets(id),
                    observer_id INTEGER,
                    observer_name TEXT,
                    starting_rank TEXT,
                    ending_rank TEXT,
                    winner TEXT,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            self.client.execute('''
                CREATE TABLE IF NOT EXISTS observation_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER REFERENCES tickets(id),
                    observer_id INTEGER,
                    observer_name TEXT,
                    starting_rank TEXT,
                    ending_rank TEXT,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            print("Turso database connected and tables ready")
        except Exception as e:
            print(f"Turso connection error: {e}")
            self.client = None
    
    async def create_ticket(self, channel_id: int, user_id: int, ticket_type: str, 
                           opponent: Optional[str] = None, private_link: Optional[str] = None) -> int:
        if not self.client:
            return 0
        result = self.client.execute('''
            INSERT INTO tickets (channel_id, user_id, ticket_type, opponent, private_link)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
        ''', (channel_id, user_id, ticket_type, opponent, private_link))
        return result.fetchone()[0]
    
    async def close_ticket(self, channel_id: int, closed_by: int):
        if not self.client:
            return
        self.client.execute('''
            UPDATE tickets 
            SET status = 'closed', closed_at = CURRENT_TIMESTAMP, closed_by = ?
            WHERE channel_id = ?
        ''', (closed_by, channel_id))
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        if not self.client:
            return None
        result = self.client.execute('''
            SELECT * FROM tickets WHERE channel_id = ?
        ''', (channel_id,))
        row = result.fetchone()
        if not row:
            return None
        columns = ['id', 'channel_id', 'user_id', 'ticket_type', 'status', 'created_at', 'closed_at', 'closed_by', 'opponent', 'private_link']
        return dict(zip(columns, row))
    
    async def add_ranked_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                starting_rank: str, ending_rank: str, winner: str, note: Optional[str] = None):
        if not self.client:
            return
        self.client.execute('''
            INSERT INTO ranked_results (ticket_id, observer_id, observer_name, starting_rank, ending_rank, winner, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (ticket_id, observer_id, observer_name, starting_rank, ending_rank, winner, note))
    
    async def add_observation_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                     starting_rank: str, ending_rank: str, note: Optional[str] = None):
        if not self.client:
            return
        self.client.execute('''
            INSERT INTO observation_results (ticket_id, observer_id, observer_name, starting_rank, ending_rank, note)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (ticket_id, observer_id, observer_name, starting_rank, ending_rank, note))
    
    async def get_user_history(self, user_id: int, limit: int = 10) -> Dict[str, List]:
        if not self.client:
            return {'ranked': [], 'observations': []}
        
        ranked = self.client.execute('''
            SELECT t.*, r.observer_name, r.starting_rank, r.ending_rank, r.winner, r.note, r.created_at as result_date
            FROM tickets t
            JOIN ranked_results r ON t.id = r.ticket_id
            WHERE t.user_id = ? AND t.status = 'closed'
            ORDER BY t.closed_at DESC
            LIMIT ?
        ''', (user_id, limit))
        
        obs = self.client.execute('''
            SELECT t.*, o.observer_name, o.starting_rank, o.ending_rank, o.note, o.created_at as result_date
            FROM tickets t
            JOIN observation_results o ON t.id = o.ticket_id
            WHERE t.user_id = ? AND t.status = 'closed'
            ORDER BY t.closed_at DESC
            LIMIT ?
        ''', (user_id, limit))
        
        t_columns = ['id', 'channel_id', 'user_id', 'ticket_type', 'status', 'created_at', 'closed_at', 'closed_by', 'opponent', 'private_link']
        r_columns = t_columns + ['observer_name', 'starting_rank', 'ending_rank', 'winner', 'note', 'result_date']
        
        return {
            'ranked': [dict(zip(r_columns, row)) for row in ranked.fetchall()],
            'observations': [dict(zip(r_columns, row)) for row in obs.fetchall()]
        }
    
    async def clear_ranked_history(self, user_id: int) -> int:
        if not self.client:
            return 0
        result = self.client.execute('''
            SELECT id FROM tickets WHERE user_id = ? AND ticket_type = 'Ranked 1v1'
        ''', (user_id,))
        ids = [row[0] for row in result.fetchall()]
        if not ids:
            return 0
        
        placeholders = ','.join(['?'] * len(ids))
        self.client.execute(f'DELETE FROM ranked_results WHERE ticket_id IN ({placeholders})', ids)
        self.client.execute(f'DELETE FROM tickets WHERE id IN ({placeholders})', ids)
        return len(ids)
    
    async def clear_observation_history(self, user_id: int) -> int:
        if not self.client:
            return 0
        result = self.client.execute('''
            SELECT id FROM tickets WHERE user_id = ? AND ticket_type = 'Personal Observation'
        ''', (user_id,))
        ids = [row[0] for row in result.fetchall()]
        if not ids:
            return 0
        
        placeholders = ','.join(['?'] * len(ids))
        self.client.execute(f'DELETE FROM observation_results WHERE ticket_id IN ({placeholders})', ids)
        self.client.execute(f'DELETE FROM tickets WHERE id IN ({placeholders})', ids)
        return len(ids)
    
    async def clear_all_history(self, user_id: int) -> tuple:
        ranked = await self.clear_ranked_history(user_id)
        obs = await self.clear_observation_history(user_id)
        return (ranked, obs)