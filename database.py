import aiosqlite
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

class Database:
    def __init__(self, db_path: str = 'tickets.db'):
        self.db_path = db_path
    
    async def init(self):
        """Initialize database tables"""
        async with aiosqlite.connect(self.db_path) as db:
            # Tickets table
            await db.execute('''
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
            
            # Ranked 1v1 results table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS ranked_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    observer_id INTEGER,
                    observer_name TEXT,
                    starting_rank TEXT,
                    ending_rank TEXT,
                    winner TEXT,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
                )
            ''')
            
            # Personal observation results table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS observation_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    observer_id INTEGER,
                    observer_name TEXT,
                    starting_rank TEXT,
                    ending_rank TEXT,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
                )
            ''')
            
            # Add note column to existing tables if they don't have it
            try:
                await db.execute('ALTER TABLE ranked_results ADD COLUMN note TEXT')
            except:
                pass
            
            try:
                await db.execute('ALTER TABLE observation_results ADD COLUMN note TEXT')
            except:
                pass
            
            await db.commit()
    
    async def create_ticket(self, channel_id: int, user_id: int, ticket_type: str, 
                           opponent: Optional[str] = None, private_link: Optional[str] = None) -> int:
        """Create a new ticket record"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO tickets (channel_id, user_id, ticket_type, opponent, private_link)
                VALUES (?, ?, ?, ?, ?)
            ''', (channel_id, user_id, ticket_type, opponent, private_link))
            await db.commit()
            return cursor.lastrowid
    
    async def close_ticket(self, channel_id: int, closed_by: int):
        """Mark ticket as closed"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE tickets 
                SET status = 'closed', closed_at = CURRENT_TIMESTAMP, closed_by = ?
                WHERE channel_id = ?
            ''', (closed_by, channel_id))
            await db.commit()
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        """Get ticket info by channel ID"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM tickets WHERE channel_id = ?
            ''', (channel_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def add_ranked_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                starting_rank: str, ending_rank: str, winner: str, note: Optional[str] = None):
        """Add ranked 1v1 result"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO ranked_results (ticket_id, observer_id, observer_name, starting_rank, ending_rank, winner, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (ticket_id, observer_id, observer_name, starting_rank, ending_rank, winner, note))
            await db.commit()
    
    async def add_observation_result(self, ticket_id: int, observer_id: int, observer_name: str,
                                     starting_rank: str, ending_rank: str, note: Optional[str] = None):
        """Add personal observation result"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO observation_results (ticket_id, observer_id, observer_name, starting_rank, ending_rank, note)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (ticket_id, observer_id, observer_name, starting_rank, ending_rank, note))
            await db.commit()
    
    async def get_user_history(self, user_id: int, limit: int = 10) -> Dict[str, List]:
        """Get user's ticket history (both ranked and observation)"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Get ranked history
            ranked_history = []
            async with db.execute('''
                SELECT t.*, r.observer_name, r.starting_rank, r.ending_rank, r.winner, r.note, r.created_at as result_date
                FROM tickets t
                JOIN ranked_results r ON t.id = r.ticket_id
                WHERE t.user_id = ? AND t.status = 'closed'
                ORDER BY t.closed_at DESC
                LIMIT ?
            ''', (user_id, limit)) as cursor:
                async for row in cursor:
                    ranked_history.append(dict(row))
            
            # Get observation history
            obs_history = []
            async with db.execute('''
                SELECT t.*, o.observer_name, o.starting_rank, o.ending_rank, o.note, o.created_at as result_date
                FROM tickets t
                JOIN observation_results o ON t.id = o.ticket_id
                WHERE t.user_id = ? AND t.status = 'closed'
                ORDER BY t.closed_at DESC
                LIMIT ?
            ''', (user_id, limit)) as cursor:
                async for row in cursor:
                    obs_history.append(dict(row))
            
            return {
                'ranked': ranked_history,
                'observations': obs_history
            }
    
    async def clear_ranked_history(self, user_id: int) -> int:
        """Clear all ranked 1v1 history for a user"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                SELECT id FROM tickets 
                WHERE user_id = ? AND ticket_type = 'Ranked 1v1'
            ''', (user_id,))
            rows = await cursor.fetchall()
            ticket_ids = [row[0] for row in rows]
            
            if not ticket_ids:
                return 0
            
            placeholders = ','.join('?' * len(ticket_ids))
            await db.execute(f'''
                DELETE FROM ranked_results 
                WHERE ticket_id IN ({placeholders})
            ''', ticket_ids)
            
            await db.execute(f'''
                DELETE FROM tickets 
                WHERE id IN ({placeholders})
            ''', ticket_ids)
            
            await db.commit()
            return len(ticket_ids)
    
    async def clear_observation_history(self, user_id: int) -> int:
        """Clear all personal observation history for a user"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                SELECT id FROM tickets 
                WHERE user_id = ? AND ticket_type = 'Personal Observation'
            ''', (user_id,))
            rows = await cursor.fetchall()
            ticket_ids = [row[0] for row in rows]
            
            if not ticket_ids:
                return 0
            
            placeholders = ','.join('?' * len(ticket_ids))
            await db.execute(f'''
                DELETE FROM observation_results 
                WHERE ticket_id IN ({placeholders})
            ''', ticket_ids)
            
            await db.execute(f'''
                DELETE FROM tickets 
                WHERE id IN ({placeholders})
            ''', ticket_ids)
            
            await db.commit()
            return len(ticket_ids)
    
    async def clear_all_history(self, user_id: int) -> tuple:
        """Clear both ranked and observation history for a user"""
        ranked = await self.clear_ranked_history(user_id)
        obs = await self.clear_observation_history(user_id)
        return (ranked, obs)