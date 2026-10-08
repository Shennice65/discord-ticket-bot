from .connection import ConnectionMixin
from .settings import SettingsMixin
from .ladder import LadderMixin
from .tickets import TicketsMixin
from .history import HistoryMixin
from .admin import AdminMixin
from .clips import ClipsMixin
from .betting import BettingMixin
from .security import SecurityMixin


class Database(ConnectionMixin, SettingsMixin, LadderMixin, TicketsMixin, HistoryMixin, AdminMixin, ClipsMixin, BettingMixin, SecurityMixin):
    def __init__(self):
        super().__init__()
