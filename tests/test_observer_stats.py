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
