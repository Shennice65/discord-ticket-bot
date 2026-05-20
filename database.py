import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config

class Database:
    def __init__(self):
        self.url = Config.TURSO_DATABASE_URL
        self.token = Config.TURSO_AUTH_TOKEN
        self.base_url = None
        self.headers = None
        
        if self.url and self.token:
            # Convert libsql:// to https:// for HTTP API
            self.base_url = self.url.replace("libsql://", "https://")
            self.headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
    
    async def _execute(self, sql: str, params: tuple = None):
        """Execute SQL via HTTP API"""
        import aiohttp
        
        if not self.base_url:
            print("Turso not configured")
            return None
        
        url = f"{self.base_url}/v2/pipeline"
        statements = [{"sql": sql}]
        if params:
            statements[0]["args"] = list(params)
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"statements": statements}, headers=self.headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"Turso error {resp.status}: {text}")
                    return None
                return await resp.json()
    
    async def init(self):
        """Initialize database tables"""
        if not self.base_url:
            print("Warning: Turso credentials not set")
            return
        
        try:
            await self._execute('''
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
            
            await self._execute('''
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
            
            await self._execute('''
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
    
    async def create_ticket(self, channel_id: int, user_id: int, ticket_type: str, 
                           opponent: Optional[str] = None, private_link: Optional[str] = None) -> int:
        result = await self._execute(
            'INSERT INTO tickets (channel_id, user_id, ticket_type, opponent, private_link) VALUES (?, ?, ?, ?, ?) RETURNING id',
            (channel_id, user_id, ticket_type, opponent, private_link)
        )
        if result and result.get("results"):
            rows = result["results"][0].get("rows", [])
            if rows:
                return rows[0][0]["value"]
        return 0
    
    async def close_ticket(self, channel_id: int, closed_by: int):
        await self._execute(
            'UPDATE tickets SET status = ?, closed_at = CURRENT_TIMESTAMP, closed_by = ? WHERE channel_id = ?',
            ('closed', closed_by, channel_id)
        )
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        result = await self._execute(
            'SELECT * FROM tickets WHERE channel_id = ?',
            (channel_id,)
        )
        if result and result.get("results"):
            rows = result["results"][0].get("rows", [])
            if rows:
                cols = result["results"][0]["columns"]
                return {cols[i]: row[i]["value"] for i, row in enumerate(rows[0])}
        return None
    
    async def add_ranked_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                starting_rank: str, ending_rank: str, winner: str, note: Optional[str] = None):
        await self._execute(
            'INSERT INTO ranked_results (ticket_id, observer_id, observer_name, starting_rank, ending_rank, winner, note) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (ticket_id, observer_id, observer_name, starting_rank, ending_rank, winner, note)
        )
    
    async def add_observation_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                     starting_rank: str, ending_rank: str, note: Optional[str] = None):
        await self._execute(
            'INSERT INTO observation_results (ticket_id, observer_id, observer_name, starting_rank, ending_rank, note) VALUES (?, ?, ?, ?, ?, ?)',
            (ticket_id, observer_id, observer_name, starting_rank, ending_rank, note)
        )
    
    async def get_user_history(self, user_id: int, limit: int = 10) -> Dict[str, List]:
        ranked = await self._execute('''
            SELECT t.*, r.observer_name, r.starting_rank, r.ending_rank, r.winner, r.note, r.created_at as result_date
            FROM tickets t
            JOIN ranked_results r ON t.id = r.ticket_id
            WHERE t.user_id = ? AND t.status = 'closed'
            ORDER BY t.closed_at DESC
            LIMIT ?
        ''', (user_id, limit))
        
        obs = await self._execute('''
            SELECT t.*, o.observer_name, o.starting_rank, o.ending_rank, o.note, o.created_at as result_date
            FROM tickets t
            JOIN observation_results o ON t.id = o.ticket_id
            WHERE t.user_id = ? AND t.status = 'closed'
            ORDER BY t.closed_at DESC
            LIMIT ?
        ''', (user_id, limit))
        
        def parse_rows(result):
            if not result or not result.get("results"):
                return []
            results = result["results"][0]
            cols = results["columns"]
            rows = results.get("rows", [])
            return [{cols[i]: row[i]["value"] for i in range(len(cols))} for row in rows]
        
        return {
            'ranked': parse_rows(ranked),
            'observations': parse_rows(obs)
        }
    
    async def clear_ranked_history(self, user_id: int) -> int:
        result = await self._execute(
            'SELECT id FROM tickets WHERE user_id = ? AND ticket_type = ?',
            (user_id, 'Ranked 1v1')
        )
        if not result or not result.get("results"):
            return 0
        
        rows = result["results"][0].get("rows", [])
        ids = [row[0]["value"] for row in rows]
        if not ids:
            return 0
        
        placeholders = ','.join(['?'] * len(ids))
        await self._execute(f'DELETE FROM ranked_results WHERE ticket_id IN ({placeholders})', tuple(ids))
        await self._execute(f'DELETE FROM tickets WHERE id IN ({placeholders})', tuple(ids))
        return len(ids)
    
    async def clear_observation_history(self, user_id: int) -> int:
        result = await self._execute(
            'SELECT id FROM tickets WHERE user_id = ? AND ticket_type = ?',
            (user_id, 'Personal Observation')
        )
        if not result or not result.get("results"):
            return 0
        
        rows = result["results"][0].get("rows", [])
        ids = [row[0]["value"] for row in rows]
        if not ids:
            return 0
        
        placeholders = ','.join(['?'] * len(ids))
        await self._execute(f'DELETE FROM observation_results WHERE ticket_id IN ({placeholders})', tuple(ids))
        await self._execute(f'DELETE FROM tickets WHERE id IN ({placeholders})', tuple(ids))
        return len(ids)
    
    async def clear_all_history(self, user_id: int) -> tuple:
        ranked = await self.clear_ranked_history(user_id)
        obs = await self.clear_observation_history(user_id)
        return (ranked, obs)