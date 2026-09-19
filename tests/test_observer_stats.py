import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_db():
    """Create a mock database with the collections used by HistoryMixin."""
    db = MagicMock()
    db.ranked_results = AsyncMock()
    db.observation_results = AsyncMock()
    db.ranked_results.count_documents = AsyncMock(return_value=0)
    db.observation_results.count_documents = AsyncMock(return_value=0)
    return db

def test_observer_total_observations_zero(mock_db):
    """Observer with no results returns 0."""
    from database.history import HistoryMixin

    mixin = HistoryMixin.__new__(HistoryMixin)
    mixin.ranked_results = mock_db.ranked_results
    mixin.observation_results = mock_db.observation_results

    mock_db.ranked_results.count_documents.return_value = 0
    mock_db.observation_results.count_documents.return_value = 0

    result = asyncio.get_event_loop().run_until_complete(
        mixin.get_observer_total_observations(12345)
    )
    assert result == 0

def test_observer_total_observations_combined(mock_db):
    """Observer total is ranked + observation results combined."""
    from database.history import HistoryMixin

    mixin = HistoryMixin.__new__(HistoryMixin)
    mixin.ranked_results = mock_db.ranked_results
    mixin.observation_results = mock_db.observation_results

    mock_db.ranked_results.count_documents.return_value = 5
    mock_db.observation_results.count_documents.return_value = 3

    result = asyncio.get_event_loop().run_until_complete(
        mixin.get_observer_total_observations(12345)
    )
    assert result == 8

def test_observer_stats_embed_fields():
    """Embed contains the expected fields for an observer."""
    from utils.embeds import TicketEmbeds

    member = MagicMock()
    member.display_name = "TestObserver"
    member.mention = "<@12345>"
    member.id = 12345
    member.status = MagicMock()
    member.status.value = "online"
    avatar_mock = MagicMock()
    avatar_mock.url = "https://example.com/avatar.png"
    member.display_avatar = avatar_mock

    embed = TicketEmbeds.observer_stats_embed(
        member=member,
        roblox_avatar_url="https://roblox.com/avatar.png",
        current_rank="Champions 2",
        total_observations=15,
    )

    assert embed.title == "Observer Stats"
    field_names = [f.name for f in embed.fields]
    assert "Observer" in field_names
    assert "Current Rank" in field_names
    assert "Total Observations" in field_names
    assert "Status" in field_names

def test_observer_stats_embed_offline():
    """Status shows Offline when member is offline."""
    from utils.embeds import TicketEmbeds

    member = MagicMock()
    member.display_name = "OfflineObs"
    member.mention = "<@99999>"
    member.id = 99999
    member.status = MagicMock()
    member.status.value = "offline"
    avatar_mock = MagicMock()
    avatar_mock.url = "https://example.com/avatar.png"
    member.display_avatar = avatar_mock

    embed = TicketEmbeds.observer_stats_embed(
        member=member,
        roblox_avatar_url="",
        current_rank="Unranked",
        total_observations=0,
    )

    status_field = next(f for f in embed.fields if f.name == "Status")
    assert "Offline" in status_field.value

def test_observer_stats_embed_no_roblox_avatar():
    """When no Roblox avatar, falls back to Discord avatar for thumbnail."""
    from utils.embeds import TicketEmbeds

    member = MagicMock()
    member.display_name = "NoRoblox"
    member.mention = "<@11111>"
    member.id = 11111
    member.status = MagicMock()
    member.status.value = "online"
    avatar_mock = MagicMock()
    avatar_mock.url = "https://cdn.discordapp.com/avatar.png"
    member.display_avatar = avatar_mock

    embed = TicketEmbeds.observer_stats_embed(
        member=member,
        roblox_avatar_url="",
        current_rank="Elites 1",
        total_observations=5,
    )

    assert embed.thumbnail.url == "https://cdn.discordapp.com/avatar.png"
