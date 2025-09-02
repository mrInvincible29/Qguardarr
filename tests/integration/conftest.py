"""Configuration and fixtures for integration tests"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from src.config import (
    CrossSeedSettings,
    GlobalSettings,
    LoggingSettings,
    QBittorrentSettings,
    QguardarrConfig,
    RollbackSettings,
    TrackerConfig,
)

from .docker_utils import (
    DockerManager,
    QBittorrentHelper,
    QguardarrHelper,
    skip_if_no_compose,
    skip_if_no_docker,
)

# Set up logging for integration tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def docker_manager():
    """Session-scoped Docker manager"""
    manager = DockerManager()

    # Skip if Docker not available
    if not manager.is_docker_available() or not manager.is_compose_available():
        pytest.skip("Docker or Docker Compose not available")

    yield manager

    # Cleanup after all tests
    manager.cleanup_containers()


@pytest.fixture(scope="session")
async def docker_services(docker_manager):
    """Start Docker services for the test session"""
    logger.info("🐳 Starting Docker services for integration tests...")

    try:
        # Cleanup any existing containers
        docker_manager.cleanup_containers()

        # Start containers
        if not docker_manager.start_containers():
            pytest.skip("Failed to start Docker containers")

        # Wait for qBittorrent
        if not await docker_manager.wait_for_service(
            "qBittorrent", "http://localhost:8080", timeout=90
        ):
            pytest.skip("qBittorrent failed to start")

        # Initialize qBittorrent (optional - client can handle temp passwords)
        try:
            if not await docker_manager.initialize_qbittorrent():
                logger.info("qBittorrent password not set, will use temporary password")
        except Exception as e:
            logger.info(f"qBittorrent initialization skipped: {e}")
            logger.info("Client will authenticate with temporary password")

        # Wait for Qguardarr (if running)
        qguardarr_ready = await docker_manager.wait_for_service(
            "Qguardarr", "http://localhost:8089/health", timeout=60
        )

        services_info = {
            "qbittorrent": True,
            "qguardarr": qguardarr_ready,
            "docker_manager": docker_manager,
        }

        logger.info(f"✅ Docker services ready: {services_info}")
        yield services_info

    except Exception as e:
        logger.error(f"❌ Failed to setup Docker services: {e}")

        # Show container logs for debugging
        try:
            qbit_logs = docker_manager.get_container_logs("qbittorrent-test")
            qguardarr_logs = docker_manager.get_container_logs("qguardarr-test")

            logger.error("qBittorrent logs:")
            logger.error(qbit_logs[:1000])  # Limit log output

            logger.error("Qguardarr logs:")
            logger.error(qguardarr_logs[:1000])

        except Exception:
            pass

        pytest.skip(f"Docker services setup failed: {e}")


@pytest.fixture
async def qbittorrent_client(docker_services):
    """qBittorrent helper client"""
    if not docker_services.get("qbittorrent"):
        pytest.skip("qBittorrent not available")

    helper = QBittorrentHelper()

    # Wait a bit more and verify health
    if not await helper.is_healthy():
        # Try waiting a bit more
        await asyncio.sleep(10)
        if not await helper.is_healthy():
            pytest.skip("qBittorrent not healthy")

    yield helper


@pytest.fixture
async def qguardarr_client(docker_services):
    """Qguardarr helper client"""
    if not docker_services.get("qguardarr"):
        pytest.skip("Qguardarr service not available")

    helper = QguardarrHelper()

    if not await helper.is_healthy():
        pytest.skip("Qguardarr service not healthy")

    yield helper


@pytest.fixture
def public_torrents_data():
    """Load public domain test torrents"""
    torrents_file = (
        Path(__file__).parent.parent.parent / "test-data" / "public_torrents.json"
    )

    if not torrents_file.exists():
        # Return minimal test data if file doesn't exist
        return [
            {
                "name": "Ubuntu 20.04 Desktop",
                "magnet": "magnet:?xt=urn:btih:5e7a6ccf8f7b2a2f8c8c8c8c8c8c8c8c8c8c8c8c",
                "size_mb": 50,
                "tracker": "http://torrent.ubuntu.com:6969/announce",
            }
        ]

    try:
        with open(torrents_file) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load public_torrents.json: {e}")
        return []


@pytest.fixture
def integration_config(tmp_path):
    """Integration test configuration"""
    return QguardarrConfig(
        **{
            "global": GlobalSettings(
                update_interval=60,
                active_torrent_threshold_kb=1,
                max_api_calls_per_cycle=1000,
                differential_threshold=0.15,
                rollout_percentage=100,
                host="localhost",
                port=8089,
            ),
            "qbittorrent": QBittorrentSettings(
                host="localhost",
                port=8080,
                username="admin",
                password="adminpass123",
                timeout=30,
            ),
            "trackers": [
                TrackerConfig(
                    id="archive_org",
                    name="Archive.org",
                    pattern=".*archive\\.org.*",
                    max_upload_speed=5 * 1024 * 1024,  # 5 MB/s
                    priority=8,
                ),
                TrackerConfig(
                    id="ubuntu",
                    name="Ubuntu",
                    pattern=".*ubuntu.*",
                    max_upload_speed=10 * 1024 * 1024,  # 10 MB/s
                    priority=10,
                ),
                TrackerConfig(
                    id="opentrackr",
                    name="OpenTrackr",
                    pattern=".*opentrackr.*",
                    max_upload_speed=3 * 1024 * 1024,  # 3 MB/s
                    priority=5,
                ),
                TrackerConfig(
                    id="default",
                    name="Default",
                    pattern=".*",  # Catch-all
                    max_upload_speed=2 * 1024 * 1024,  # 2 MB/s
                    priority=1,
                ),
            ],
            "rollback": RollbackSettings(
                database_path=str(tmp_path / "test_rollback.db"), track_all_changes=True
            ),
            "cross_seed": CrossSeedSettings(
                enabled=False, url=None, api_key=None, timeout=30
            ),
            "logging": LoggingSettings(
                level="INFO",
                file=str(tmp_path / "test.log"),
                max_size_mb=10,
                backup_count=3,
            ),
        }
    )


# Service checker for legacy compatibility
@pytest.fixture
async def service_checker(docker_services):
    """Legacy service checker fixture"""

    class ServiceChecker:
        def __init__(self, services_info):
            self.services_info = services_info

        async def check_qbittorrent(self) -> bool:
            if not self.services_info.get("qbittorrent"):
                return False
            helper = QBittorrentHelper()
            return await helper.is_healthy()

        async def check_qguardarr(self) -> bool:
            if not self.services_info.get("qguardarr"):
                return False
            helper = QguardarrHelper()
            return await helper.is_healthy()

    return ServiceChecker(docker_services)


# Utility functions for integration tests
def assert_torrent_limit_within_range(
    actual: int, expected: int, tolerance: float = 0.01
):
    """Assert torrent limit is within acceptable range"""
    if expected == -1:  # Unlimited
        assert actual == -1 or actual == 0
    elif expected == 0:  # No upload
        assert actual == 0
    else:
        # Allow small differences due to qBittorrent's internal handling
        min_expected = int(expected * (1 - tolerance))
        max_expected = int(expected * (1 + tolerance))
        assert (
            min_expected <= actual <= max_expected
        ), f"Expected {expected}, got {actual} (tolerance: {tolerance})"


# Test environment information
def pytest_configure(config):
    """Configure pytest for integration tests"""
    # Add markers
    config.addinivalue_line("markers", "docker: mark test as requiring Docker")
    config.addinivalue_line(
        "markers", "qbittorrent: mark test as requiring qBittorrent"
    )
    config.addinivalue_line("markers", "qguardarr: mark test as requiring Qguardarr")
    config.addinivalue_line("markers", "slow: mark test as slow running")

    # Set environment variables for testing
    os.environ.setdefault("PYTEST_INTEGRATION", "1")


def pytest_collection_modifyitems(config, items):
    """Modify test collection for integration tests"""
    # Add docker marker to all tests in this directory
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.docker)

            # Add slow marker to certain tests
            if any(
                keyword in item.name
                for keyword in ["end_to_end", "performance", "load"]
            ):
                item.add_marker(pytest.mark.slow)


@pytest.fixture(autouse=True)
def integration_test_setup():
    """Automatic setup for all integration tests"""
    logger.info("🧪 Running integration test...")
    yield
    logger.info("✅ Integration test completed")


# Mock helpers for tests that don't need real services
@pytest.fixture
def mock_qbit_client():
    """Mock qBittorrent client for unit-style integration tests"""
    client = AsyncMock()
    client.config.host = "localhost"
    client.config.port = 8080
    client.authenticated = True
    client.needs_update.return_value = True

    return client


@pytest.fixture
def mock_allocation_engine():
    """Mock allocation engine for webhook tests"""
    engine = AsyncMock()
    engine.mark_torrent_for_check.return_value = None
    engine.schedule_tracker_update.return_value = None
    engine.handle_torrent_deletion.return_value = None

    return engine
