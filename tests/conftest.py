"""Global test configuration and fixtures for pytest"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import Mock, AsyncMock

import pytest

from src.config import (
    QguardarrConfig, 
    GlobalSettings, 
    QBittorrentSettings, 
    TrackerConfig, 
    RollbackSettings,
    CrossSeedSettings,
    LoggingSettings
)
from src.qbit_client import TorrentInfo


def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "load: mark test as load/performance test"
    )


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir():
    """Create temporary directory for test files"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_config(temp_dir) -> QguardarrConfig:
    """Basic test configuration"""
    return QguardarrConfig(**{
        "global": GlobalSettings(
            update_interval=60,  # Minimum allowed value
            active_torrent_threshold_kb=1,
            max_api_calls_per_cycle=1000,
            differential_threshold=0.1,
            rollout_percentage=100,
            host="localhost",
            port=8089
        ),
        "qbittorrent": QBittorrentSettings(
            host="localhost",
            port=8080,
            username="admin",
            password="test123",
            timeout=30
        ),
        "trackers": [
            TrackerConfig(
                id="test_tracker1",
                name="Test Tracker 1",
                pattern=".*test1\\.com.*",
                max_upload_speed=5 * 1024 * 1024,  # 5 MB/s
                priority=10
            ),
            TrackerConfig(
                id="test_tracker2", 
                name="Test Tracker 2",
                pattern=".*test2\\.com.*",
                max_upload_speed=2 * 1024 * 1024,  # 2 MB/s
                priority=5
            ),
            TrackerConfig(
                id="default",
                name="Default Tracker",
                pattern=".*",  # Catch-all
                max_upload_speed=1 * 1024 * 1024,  # 1 MB/s
                priority=1
            )
        ],
        "rollback": RollbackSettings(
            database_path=str(temp_dir / "test_rollback.db"),
            track_all_changes=True
        ),
        "cross_seed": CrossSeedSettings(
            enabled=False,
            url=None,
            api_key=None,
            timeout=15
        ),
        "logging": LoggingSettings(
            level="INFO",
            file="./logs/test.log",
            max_size_mb=10,
            backup_count=3
        )
    })


@pytest.fixture
def integration_config(temp_dir) -> QguardarrConfig:
    """Configuration for integration tests with real services"""
    return QguardarrConfig(**{
        "global": GlobalSettings(
            update_interval=60,  # Minimum allowed value
            active_torrent_threshold_kb=1,
            max_api_calls_per_cycle=1000,
            differential_threshold=0.1,
            rollout_percentage=100,
            host="localhost",
            port=8089
        ),
        "qbittorrent": QBittorrentSettings(
            host="localhost",
            port=8080,
            username="admin",
            password="adminpass123",
            timeout=30
        ),
        "trackers": [
            TrackerConfig(
                id="archive_org",
                name="Internet Archive",
                pattern=".*tracker\\.archive\\.org.*",
                max_upload_speed=5 * 1024 * 1024,
                priority=10
            ),
            TrackerConfig(
                id="ubuntu",
                name="Ubuntu Tracker",
                pattern=".*torrent\\.ubuntu\\.com.*",
                max_upload_speed=3 * 1024 * 1024,
                priority=8
            ),
            TrackerConfig(
                id="opentrackr",
                name="OpenTracker",
                pattern=".*tracker\\.opentrackr\\.org.*",
                max_upload_speed=2 * 1024 * 1024,
                priority=6
            ),
            TrackerConfig(
                id="default",
                name="Default Trackers",
                pattern=".*",
                max_upload_speed=1 * 1024 * 1024,
                priority=1
            )
        ],
        "rollback": RollbackSettings(
            database_path=str(temp_dir / "integration_rollback.db"),
            track_all_changes=True
        ),
        "cross_seed": CrossSeedSettings(
            enabled=True,
            url="http://localhost:2468/api/webhook",
            api_key="test-key",
            timeout=15
        ),
        "logging": LoggingSettings(
            level="INFO",
            file="./logs/integration_test.log",
            max_size_mb=10,
            backup_count=3
        )
    })


@pytest.fixture
def sample_torrents() -> List[TorrentInfo]:
    """Sample torrent data for testing"""
    return [
        TorrentInfo(
            hash="a1b2c3d4e5f6789a0b1c2d3e4f567890abcdef12",
            name="Test Torrent 1",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=1024 * 50,  # 50 KB/s
            priority=1,
            num_seeds=10,
            num_leechs=5,
            ratio=1.5,
            size=100 * 1024 * 1024,  # 100 MB
            completed=100 * 1024 * 1024,
            tracker="http://test1.com/announce",
            category="movies",
            tags="test,sample",
            added_on=1640995200,  # 2022-01-01
            last_activity=1640995200
        ),
        TorrentInfo(
            hash="b2c3d4e5f6789a0b1c2d3e4f567890abcdef1234",
            name="Test Torrent 2",
            state="uploading", 
            progress=0.8,
            dlspeed=1024 * 100,  # 100 KB/s
            upspeed=1024 * 25,   # 25 KB/s
            priority=1,
            num_seeds=5,
            num_leechs=10,
            ratio=0.8,
            size=500 * 1024 * 1024,  # 500 MB
            completed=400 * 1024 * 1024,  # 80% complete
            tracker="http://test2.com/announce",
            category="software",
            tags="test,linux",
            added_on=1640995200,
            last_activity=1640995200
        ),
        TorrentInfo(
            hash="c3d4e5f6789a0b1c2d3e4f567890abcdef123456",
            name="Test Torrent 3",
            state="seeding",
            progress=1.0,
            dlspeed=0,
            upspeed=1024 * 100,  # 100 KB/s
            priority=1,
            num_seeds=20,
            num_leechs=2,
            ratio=2.5,
            size=50 * 1024 * 1024,   # 50 MB
            completed=50 * 1024 * 1024,
            tracker="http://unknown-tracker.com/announce",
            category="books",
            tags="test,ebook",
            added_on=1640995200,
            last_activity=1640995200
        )
    ]


@pytest.fixture
def mock_qbit_client():
    """Mock qBittorrent client for unit tests"""
    client = AsyncMock()
    
    # Default behavior
    client.authenticated = True
    client.connect.return_value = None
    client.disconnect.return_value = None
    client.get_torrents.return_value = []
    client.get_torrent_upload_limit.return_value = 1024000  # 1MB/s
    client.set_torrent_upload_limit.return_value = None
    client.set_torrents_upload_limits_batch.return_value = None
    client.needs_update.return_value = True
    client.get_stats.return_value = {
        "api_calls": 0,
        "api_failures": 0,
        "last_error": None
    }
    
    return client


@pytest.fixture  
def mock_tracker_matcher():
    """Mock tracker matcher for unit tests"""
    matcher = Mock()
    
    # Default tracker matching behavior
    def match_tracker_side_effect(url: str) -> str:
        if "test1.com" in url:
            return "test_tracker1"
        elif "test2.com" in url:
            return "test_tracker2"
        else:
            return "default"
    
    matcher.match_tracker.side_effect = match_tracker_side_effect
    
    # Default tracker config behavior
    def get_tracker_config_side_effect(tracker_id: str):
        configs = {
            "test_tracker1": Mock(
                id="test_tracker1",
                name="Test Tracker 1",
                max_upload_speed=5 * 1024 * 1024,
                priority=10
            ),
            "test_tracker2": Mock(
                id="test_tracker2", 
                name="Test Tracker 2",
                max_upload_speed=2 * 1024 * 1024,
                priority=5
            ),
            "default": Mock(
                id="default",
                name="Default",
                max_upload_speed=1 * 1024 * 1024,
                priority=1
            )
        }
        return configs.get(tracker_id)
    
    matcher.get_tracker_config.side_effect = get_tracker_config_side_effect
    matcher.get_all_tracker_configs.return_value = [
        get_tracker_config_side_effect("test_tracker1"),
        get_tracker_config_side_effect("test_tracker2"), 
        get_tracker_config_side_effect("default")
    ]
    matcher.get_cache_stats.return_value = {"hits": 0, "misses": 0}
    
    return matcher


@pytest.fixture
def mock_rollback_manager():
    """Mock rollback manager for unit tests"""
    manager = AsyncMock()
    
    manager.initialize.return_value = None
    manager.record_change.return_value = None
    manager.record_batch_changes.return_value = None
    manager.rollback_all_changes.return_value = 0
    manager.get_stats.return_value = {
        "changes_recorded": 0,
        "rollbacks_performed": 0
    }
    
    return manager


@pytest.fixture
def public_torrents_data():
    """Load public domain torrents data"""
    torrents_file = Path(__file__).parent / "test-data" / "public_torrents.json"
    
    if torrents_file.exists():
        with open(torrents_file) as f:
            return json.load(f)
    else:
        # Return sample data if file doesn't exist
        return [
            {
                "name": "Ubuntu 22.04.3 Desktop",
                "magnet": "magnet:?xt=urn:btih:sample123&dn=ubuntu-22.04.3-desktop-amd64.iso&tr=http://torrent.ubuntu.com:6969/announce",
                "size_mb": 4800,
                "tracker": "torrent.ubuntu.com",
                "category": "linux-distro"
            },
            {
                "name": "Internet Archive - Night of the Living Dead",
                "magnet": "magnet:?xt=urn:btih:sample456&dn=night_of_the_living_dead_1968&tr=http://tracker.archive.org:6969/announce",
                "size_mb": 600,
                "tracker": "tracker.archive.org",
                "category": "movies"
            }
        ]


@pytest.fixture
def webhook_events():
    """Sample webhook events for testing"""
    return [
        {
            "event": "complete",
            "hash": "a1b2c3d4e5f6789a0b1c2d3e4f567890abcdef12",
            "name": "Test Torrent Complete",
            "tracker": "http://test1.com/announce",
            "category": "movies",
            "save_path": "/downloads/movies"
        },
        {
            "event": "add",
            "hash": "b2c3d4e5f6789a0b1c2d3e4f567890abcdef1234",
            "name": "Test Torrent Added",
            "tracker": "http://test2.com/announce",
            "category": "software"
        },
        {
            "event": "delete",
            "hash": "c3d4e5f6789a0b1c2d3e4f567890abcdef123456",
            "name": "Test Torrent Deleted",
            "tracker": "http://test1.com/announce"
        }
    ]


class ServiceChecker:
    """Utility to check if external services are available"""
    
    @staticmethod
    async def check_qbittorrent(host="localhost", port=8080):
        """Check if qBittorrent is available"""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://{host}:{port}", timeout=5.0)
                return response.status_code in [200, 401]  # 401 is OK (login required)
        except Exception:
            return False
    
    @staticmethod
    async def check_qguardarr(host="localhost", port=8089):
        """Check if Qguardarr is available"""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://{host}:{port}/health", timeout=5.0)
                return response.status_code == 200
        except Exception:
            return False


@pytest.fixture
def service_checker():
    """Service availability checker"""
    return ServiceChecker()


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add skip conditions"""
    # Add integration marker to tests in integration/ directory
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "load" in str(item.fspath):
            item.add_marker(pytest.mark.load)


def pytest_runtest_setup(item):
    """Setup for individual tests"""
    # Skip integration tests in CI unless explicitly requested
    if item.get_closest_marker("integration"):
        if os.getenv("SKIP_INTEGRATION", "false").lower() == "true":
            pytest.skip("Integration tests skipped (SKIP_INTEGRATION=true)")
    
    # Skip load tests unless explicitly requested  
    if item.get_closest_marker("load"):
        if not os.getenv("RUN_LOAD_TESTS", "false").lower() == "true":
            pytest.skip("Load tests skipped (set RUN_LOAD_TESTS=true to run)")


# Utility functions for tests

def assert_torrent_limit_within_range(actual_limit: int, expected_limit: int, tolerance: float = 0.1):
    """Assert torrent limit is within acceptable range"""
    lower_bound = expected_limit * (1 - tolerance)
    upper_bound = expected_limit * (1 + tolerance)
    
    assert lower_bound <= actual_limit <= upper_bound, (
        f"Limit {actual_limit} not within {tolerance:.1%} of expected {expected_limit} "
        f"(range: {lower_bound:.0f} - {upper_bound:.0f})"
    )


def assert_memory_usage_acceptable(memory_mb: float, max_mb: float = 60):
    """Assert memory usage is within acceptable limits"""
    assert memory_mb <= max_mb, f"Memory usage {memory_mb:.2f} MB exceeds limit of {max_mb} MB"


def assert_response_time_acceptable(response_time_ms: float, max_ms: float = 100):
    """Assert response time is acceptable"""
    assert response_time_ms <= max_ms, f"Response time {response_time_ms:.1f} ms exceeds limit of {max_ms} ms"