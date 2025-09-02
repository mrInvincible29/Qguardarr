"""Integration tests for rollback functionality with real database"""

import asyncio
import os
import tempfile
import time
from pathlib import Path
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
from src.rollback import RollbackManager


@pytest.mark.integration
class TestRollbackIntegration:
    """Test rollback functionality with real SQLite database"""

    @pytest.fixture
    async def rollback_manager(self, temp_dir):
        """Create a real rollback manager with SQLite database"""
        db_path = temp_dir / "test_rollback.db"

        rollback_config = RollbackSettings(
            database_path=str(db_path), track_all_changes=True
        )

        manager = RollbackManager(rollback_config)
        await manager.initialize()

        yield manager

        # Cleanup - no close method needed
        if db_path.exists():
            db_path.unlink()

    @pytest.mark.asyncio
    async def test_basic_rollback_functionality(self, rollback_manager):
        """
        Test basic rollback operations with real database

        This is a critical Phase 1 requirement - rollback must work
        """
        # Test data
        torrent_hashes = [
            "a1b2c3d4e5f6789a0b1c2d3e4f567890abcdef12",
            "b2c3d4e5f6789a0b1c2d3e4f567890abcdef1234",
        ]

        # Record some limit changes as tuples
        # Format: (hash, old_limit, new_limit, tracker_id, reason)
        changes = []
        for i, hash_ in enumerate(torrent_hashes):
            change = (
                hash_,
                1048576,  # old_limit - 1 MB/s
                2097152,  # new_limit - 2 MB/s
                f"test_tracker_{i + 1}",
                "test_change",  # reason
            )
            changes.append(change)

        # Record the changes
        changes_recorded = await rollback_manager.record_batch_changes(changes)

        assert changes_recorded > 0, "Changes should be recorded"
        assert changes_recorded == len(
            changes
        ), f"Expected {len(changes)} changes, recorded {changes_recorded}"

        # Verify changes were recorded using stats
        stats = await rollback_manager.get_rollback_stats()
        assert stats["total_entries"] >= len(
            changes
        ), "Should have recorded all changes"

        # Get rollback data to restore original limits
        rollback_data = await rollback_manager.get_rollback_data_for_application()

        # Verify rollback data contains correct original limits
        for hash_, old_limit, new_limit, tracker, reason in changes:
            assert hash_ in rollback_data, f"Hash {hash_} should be in rollback data"
            assert (
                rollback_data[hash_] == old_limit
            ), f"Original limit should be {old_limit}"

        # Execute rollback (this just marks as restored, doesn't actually apply limits)
        rollback_count = await rollback_manager.rollback_all_changes("test_rollback")

        assert rollback_count > 0, "Rollback should affect some changes"

        print(
            f"✅ Successfully recorded {changes_recorded} changes and prepared rollback for {rollback_count} torrents"
        )

    @pytest.mark.asyncio
    async def test_database_persistence(self, temp_dir):
        """Test that rollback data persists across database connections"""

        db_path = temp_dir / "persistence_test.db"

        # First connection - record some changes
        rollback_config = RollbackSettings(
            database_path=str(db_path), track_all_changes=True
        )

        manager1 = RollbackManager(rollback_config)
        await manager1.initialize()

        changes = [
            ("persistence_test_hash", 1048576, 2097152, "persistence_tracker", "test")
        ]

        changes_recorded = await manager1.record_batch_changes(changes)
        assert changes_recorded == 1, "Should record 1 change"

        stats1 = await manager1.get_rollback_stats()

        # Second connection - verify data persists
        manager2 = RollbackManager(rollback_config)
        await manager2.initialize()

        stats2 = await manager2.get_rollback_stats()

        # Data should persist
        assert stats2["total_entries"] >= stats1["total_entries"]

        # Should be able to get rollback data from first session
        rollback_data = await manager2.get_rollback_data_for_application()
        assert "persistence_test_hash" in rollback_data
        assert rollback_data["persistence_test_hash"] == 1048576

        # Cleanup
        if db_path.exists():
            db_path.unlink()

        print("✅ Database persistence verified")

    @pytest.mark.asyncio
    async def test_rollback_performance(self, rollback_manager):
        """Test rollback performance with many changes"""

        # Create a large batch of changes (100 torrents)
        changes = []
        for i in range(100):
            changes.append(
                (
                    f"perf_test_{i:08x}" + "a" * 32,
                    1048576 + i * 1024,  # old_limit - varying
                    2097152 + i * 2048,  # new_limit - varying
                    f"tracker_{i % 10}",  # 10 different trackers
                    "performance_test",
                )
            )

        # Measure recording performance
        start_time = time.time()
        changes_recorded = await rollback_manager.record_batch_changes(changes)
        record_time = time.time() - start_time

        # Should be fast even for 100 changes
        assert (
            record_time < 1.0
        ), f"Recording 100 changes took {record_time:.3f}s, should be <1s"
        assert changes_recorded == 100, "Should record all 100 changes"

        # Measure rollback data retrieval performance
        start_time = time.time()
        rollback_data = await rollback_manager.get_rollback_data_for_application()
        retrieval_time = time.time() - start_time

        assert len(rollback_data) == 100, "Should have rollback data for all torrents"
        assert (
            retrieval_time < 1.0
        ), f"Retrieving rollback data took {retrieval_time:.3f}s, should be <1s"

        print(
            f"✅ Performance test: Record {record_time:.3f}s, Retrieve {retrieval_time:.3f}s for 100 changes"
        )
