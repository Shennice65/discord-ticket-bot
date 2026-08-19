import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
import asyncio


from .connection import ConnectionMixin
from .settings import SettingsMixin
from .ladder import LadderMixin
from .tickets import TicketsMixin
from .history import HistoryMixin
from .admin import AdminMixin
from .clips import ClipsMixin

class Database(ConnectionMixin, SettingsMixin, LadderMixin, TicketsMixin, HistoryMixin, AdminMixin, ClipsMixin):
    def __init__(self):
        super().__init__()
