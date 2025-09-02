"""Unit tests for allocation engine"""

import hashlib
import time
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest

from src.allocation import AllocationEngine, GradualRollout, TorrentCache
from src.config import (
    CrossSeedSettings,
    GlobalSettings,
    QBittorrentSettings,
    QguardarrConfig,
    RollbackSettings,
    TrackerConfig,
)
from src.qbit_client import TorrentInfo


class TestTorrentCache:
    """Test the TorrentCache implementation"""

    def test_cache_initialization(self):
        """Test cache initialization with default capacity"""
        cache = TorrentCache(capacity=100)

        assert cache.capacity == 100
        assert cache.used_count == 0
        assert len(cache.free_slots) == 100
        assert len(cache.hashes) == 100
        assert len(cache.tracker_ids) == 100
        assert cache.upload_speeds.shape == (100,)
        assert cache.current_limits.shape == (100,)

    def test_add_torrent(self):
        """Test adding torrents to cache"""
        cache = TorrentCache(capacity=10)

        # Add first torrent
        success = cache.add_torrent(
            torrent_hash="abc123",
            tracker_id="tracker1",
            upload_speed=1024.0,
            current_limit=2048,
        )

        assert success is True
        assert cache.used_count == 1
        assert len(cache.free_slots) == 9
        assert "abc123" in cache.hash_to_index

        # Verify data was stored correctly
        index = cache.hash_to_index["abc123"]
        assert cache.hashes[index] == "abc123"
        assert cache.tracker_ids[index] == "tracker1"
        assert cache.upload_speeds[index] == 1024.0
        assert cache.current_limits[index] == 2048

    def test_cache_capacity_limit(self):
        """Test that cache respects capacity limits"""
        cache = TorrentCache(capacity=2)

        # Fill cache
        assert cache.add_torrent("hash1", "tracker1", 100.0, 1000) is True
        assert cache.add_torrent("hash2", "tracker2", 200.0, 2000) is True

        # Try to add beyond capacity
        assert cache.add_torrent("hash3", "tracker3", 300.0, 3000) is False
        assert cache.used_count == 2

    def test_update_torrent(self):
        """Test updating existing torrent data"""
        cache = TorrentCache()
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)

        # Update the torrent
        cache.update_torrent("hash1", 200.0, 2000)

        index = cache.hash_to_index["hash1"]
        assert cache.upload_speeds[index] == 200.0
        assert cache.current_limits[index] == 2000

    def test_remove_torrent(self):
        """Test removing torrents from cache"""
        cache = TorrentCache()
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)
        cache.add_torrent("hash2", "tracker2", 200.0, 2000)

        assert cache.used_count == 2

        # Remove first torrent
        success = cache.remove_torrent("hash1")

        assert success is True
        assert cache.used_count == 1
        assert "hash1" not in cache.hash_to_index
        assert len(cache.free_slots) == cache.capacity - 1

        # Try to remove non-existent torrent
        assert cache.remove_torrent("nonexistent") is False

    def test_get_tracker_id(self):
        """Test O(1) tracker lookup"""
        cache = TorrentCache()
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)

        assert cache.get_tracker_id("hash1") == "tracker1"
        assert cache.get_tracker_id("nonexistent") is None

    def test_get_current_limit(self):
        """Test getting current limit for torrent"""
        cache = TorrentCache()
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)

        assert cache.get_current_limit("hash1") == 1000
        assert cache.get_current_limit("nonexistent") is None

    def test_mark_for_update(self):
        """Test marking torrents for update"""
        cache = TorrentCache()
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)

        # Initially not marked for update
        index = cache.hash_to_index["hash1"]
        assert bool(cache.needs_update[index]) is False

        # Mark for update
        cache.mark_for_update("hash1")
        assert bool(cache.needs_update[index]) is True

    def test_get_torrents_by_tracker(self):
        """Test getting all torrents for a specific tracker"""
        cache = TorrentCache()
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)
        cache.add_torrent("hash2", "tracker1", 200.0, 2000)
        cache.add_torrent("hash3", "tracker2", 300.0, 3000)

        tracker1_torrents = cache.get_torrents_by_tracker("tracker1")

        assert len(tracker1_torrents) == 2

        # Check that we have both torrents
        hashes = [t[0] for t in tracker1_torrents]
        assert "hash1" in hashes
        assert "hash2" in hashes
        assert "hash3" not in hashes

    def test_get_torrents_needing_update(self):
        """Test getting torrents that need updates"""
        cache = TorrentCache()
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)
        cache.add_torrent("hash2", "tracker1", 200.0, 2000)

        # Mark one for update
        cache.mark_for_update("hash1")

        updates = cache.get_torrents_needing_update()

        assert len(updates) == 1
        assert updates[0] == ("hash1", 1000)

        # Should clear the flag after getting
        updates2 = cache.get_torrents_needing_update()
        assert len(updates2) == 0

    def test_cleanup_old_torrents(self):
        """Test cleanup of old torrent entries"""
        cache = TorrentCache()

        # Add torrents with different ages
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)
        cache.add_torrent("hash2", "tracker1", 200.0, 2000)

        # Manually set one to be old
        index = cache.hash_to_index["hash1"]
        cache.last_seen[index] = int(time.time()) - 3600  # 1 hour ago

        # Cleanup with 30 minute threshold
        cleaned = cache.cleanup_old_torrents(max_age_seconds=1800)

        assert cleaned == 1
        assert "hash1" not in cache.hash_to_index
        assert "hash2" in cache.hash_to_index

    def test_cache_stats(self):
        """Test cache statistics"""
        cache = TorrentCache(capacity=10)
        cache.add_torrent("hash1", "tracker1", 100.0, 1000)
        cache.add_torrent("hash2", "tracker1", 200.0, 2000)

        stats = cache.get_stats()

        assert stats["used_count"] == 2
        assert stats["free_slots"] == 8
        assert stats["capacity"] == 10
        assert stats["utilization_percent"] == 20.0


class TestGradualRollout:
    """Test gradual rollout functionality"""

    def test_rollout_initialization(self):
        """Test rollout initialization"""
        rollout = GradualRollout(rollout_percentage=25)
        assert rollout.rollout_percentage == 25

    def test_full_rollout(self):
        """Test 100% rollout - all torrents managed"""
        rollout = GradualRollout(rollout_percentage=100)

        # All torrents should be managed
        assert rollout.should_manage_torrent("hash1") is True
        assert rollout.should_manage_torrent("hash2") is True
        assert rollout.should_manage_torrent("anyhash") is True

    def test_partial_rollout_deterministic(self):
        """Test that rollout is deterministic based on hash"""
        rollout = GradualRollout(rollout_percentage=50)

        # Same hash should always give same result
        hash1_result1 = rollout.should_manage_torrent("hash1")
        hash1_result2 = rollout.should_manage_torrent("hash1")
        assert hash1_result1 == hash1_result2

        # Different hashes may give different results
        results = []
        for i in range(100):
            result = rollout.should_manage_torrent(f"hash{i}")
            results.append(result)

        # With 50% rollout, roughly half should be True
        true_count = sum(results)
        assert 30 <= true_count <= 70  # Allow some variance

    def test_zero_rollout(self):
        """Test 0% rollout edge case"""
        rollout = GradualRollout(rollout_percentage=1)  # Minimum 1%

        # Very few should be managed
        managed_count = 0
        for i in range(100):
            if rollout.should_manage_torrent(f"hash{i}"):
                managed_count += 1

        # With 1% rollout, expect very few
        assert managed_count <= 10

    def test_update_rollout_percentage(self):
        """Test updating rollout percentage"""
        rollout = GradualRollout(rollout_percentage=10)

        assert rollout.rollout_percentage == 10

        rollout.update_rollout_percentage(75)
        assert rollout.rollout_percentage == 75

        # Test bounds
        rollout.update_rollout_percentage(150)  # Should cap at 100
        assert rollout.rollout_percentage == 100

        rollout.update_rollout_percentage(-5)  # Should floor at 1
        assert rollout.rollout_percentage == 1


class TestAllocationEngine:
    """Test the main allocation engine"""

    @pytest.fixture
    def config(self):
        """Test configuration"""
        from src.config import LoggingSettings

        return QguardarrConfig(
            **{
                "global": GlobalSettings(
                    update_interval=300,
                    active_torrent_threshold_kb=10,
                    max_api_calls_per_cycle=500,
                    differential_threshold=0.2,
                    rollout_percentage=100,
                    host="localhost",
                    port=8089,
                ),
                "qbittorrent": QBittorrentSettings(
                    host="localhost",
                    port=8080,
                    username="admin",
                    password="password",
                    timeout=30,
                ),
                "trackers": [
                    TrackerConfig(
                        id="tracker1",
                        name="Tracker 1",
                        pattern=".*tracker1\\.com.*",
                        max_upload_speed=5242880,  # 5MB/s
                        priority=10,
                    ),
                    TrackerConfig(
                        id="tracker2",
                        name="Tracker 2",
                        pattern=".*tracker2\\.com.*",
                        max_upload_speed=2097152,  # 2MB/s
                        priority=5,
                    ),
                    TrackerConfig(
                        id="default",
                        name="Default",
                        pattern=".*",
                        max_upload_speed=1048576,  # 1MB/s
                        priority=1,
                    ),
                ],
                "rollback": RollbackSettings(
                    database_path="./test.db", track_all_changes=True
                ),
                "cross_seed": CrossSeedSettings(
                    enabled=False, url=None, api_key=None, timeout=15
                ),
                "logging": LoggingSettings(
                    level="INFO", file="./test.log", max_size_mb=10, backup_count=3
                ),
            }
        )

    @pytest.fixture
    def mock_qbit_client(self):
        """Mock qBittorrent client"""
        from unittest.mock import Mock

        client = AsyncMock()
        client.needs_update = Mock(return_value=True)  # Sync mock for sync method
        return client

    @pytest.fixture
    def mock_tracker_matcher(self):
        """Mock tracker matcher"""
        matcher = Mock()
        matcher.match_tracker.side_effect = lambda url: (
            "tracker1"
            if "tracker1.com" in url
            else "tracker2" if "tracker2.com" in url else "default"
        )
        matcher.get_tracker_config.side_effect = lambda tracker_id: Mock(
            id=tracker_id,
            max_upload_speed=(
                5242880
                if tracker_id == "tracker1"
                else 2097152 if tracker_id == "tracker2" else 1048576
            ),
        )
        return matcher

    @pytest.fixture
    def mock_rollback_manager(self):
        """Mock rollback manager"""
        return AsyncMock()

    @pytest.fixture
    def allocation_engine(
        self, config, mock_qbit_client, mock_tracker_matcher, mock_rollback_manager
    ):
        """Allocation engine with mocked dependencies"""
        return AllocationEngine(
            config=config,
            qbit_client=mock_qbit_client,
            tracker_matcher=mock_tracker_matcher,
            rollback_manager=mock_rollback_manager,
        )

    def test_allocation_engine_initialization(self, allocation_engine):
        """Test allocation engine initialization"""
        assert allocation_engine.cache.capacity == 5000
        assert allocation_engine.gradual_rollout.rollout_percentage == 100
        assert allocation_engine.stats["allocation_cycles"] == 0

    def test_filter_torrents_for_rollout(self, allocation_engine):
        """Test filtering torrents based on rollout percentage"""
        # Create test torrents
        torrents = [
            TorrentInfo(
                hash=f"hash{i}",
                name=f"torrent{i}",
                state="uploading",
                progress=1.0,
                dlspeed=0,
                upspeed=1000,
                priority=1,
                num_seeds=5,
                num_leechs=2,
                ratio=1.5,
                size=1000000,
                completed=1000000,
            )
            for i in range(10)
        ]

        # With 100% rollout, all should be included
        allocation_engine.gradual_rollout.rollout_percentage = 100
        filtered = allocation_engine._filter_torrents_for_rollout(torrents)
        assert len(filtered) == 10

        # With 0% rollout (actually 1% minimum), very few should be included
        allocation_engine.gradual_rollout.rollout_percentage = 1
        filtered = allocation_engine._filter_torrents_for_rollout(torrents)
        assert len(filtered) < 10  # Should be fewer

    def test_calculate_limits_phase1_single_torrent(self, allocation_engine):
        """Test Phase 1 limit calculation with single torrent per tracker"""
        torrents = [
            TorrentInfo(
                hash="hash1",
                name="torrent1",
                state="uploading",
                progress=1.0,
                dlspeed=0,
                upspeed=1000,
                priority=1,
                num_seeds=5,
                num_leechs=2,
                ratio=1.5,
                size=1000000,
                completed=1000000,
                tracker="http://tracker1.com/announce",
            )
        ]

        limits = allocation_engine._calculate_limits_phase1(torrents)

        # Single torrent should get full tracker limit
        assert limits["hash1"] == 5242880  # 5MB/s from tracker1 config

    def test_calculate_limits_phase1_multiple_torrents(self, allocation_engine):
        """Test Phase 1 limit calculation with multiple torrents per tracker"""
        torrents = [
            TorrentInfo(
                hash="hash1",
                name="torrent1",
                state="uploading",
                progress=1.0,
                dlspeed=0,
                upspeed=1000,
                priority=1,
                num_seeds=5,
                num_leechs=2,
                ratio=1.5,
                size=1000000,
                completed=1000000,
                tracker="http://tracker1.com/announce",
            ),
            TorrentInfo(
                hash="hash2",
                name="torrent2",
                state="uploading",
                progress=1.0,
                dlspeed=0,
                upspeed=1000,
                priority=1,
                num_seeds=5,
                num_leechs=2,
                ratio=1.5,
                size=1000000,
                completed=1000000,
                tracker="http://tracker1.com/announce",
            ),
            TorrentInfo(
                hash="hash3",
                name="torrent3",
                state="uploading",
                progress=1.0,
                dlspeed=0,
                upspeed=1000,
                priority=1,
                num_seeds=5,
                num_leechs=2,
                ratio=1.5,
                size=1000000,
                completed=1000000,
                tracker="http://tracker1.com/announce",
            ),
        ]

        limits = allocation_engine._calculate_limits_phase1(torrents)

        # Three torrents should share tracker limit equally
        expected_per_torrent = 5242880 // 3  # Tracker1 limit / 3 torrents
        assert limits["hash1"] == expected_per_torrent
        assert limits["hash2"] == expected_per_torrent
        assert limits["hash3"] == expected_per_torrent

    def test_calculate_limits_phase1_minimum_limit(self, allocation_engine):
        """Test that minimum limit per torrent is enforced"""
        # Create many torrents to test minimum limit
        torrents = []
        for i in range(1000):  # Many torrents to force low per-torrent limit
            torrents.append(
                TorrentInfo(
                    hash=f"hash{i}",
                    name=f"torrent{i}",
                    state="uploading",
                    progress=1.0,
                    dlspeed=0,
                    upspeed=1000,
                    priority=1,
                    num_seeds=5,
                    num_leechs=2,
                    ratio=1.5,
                    size=1000000,
                    completed=1000000,
                    tracker="http://tracker1.com/announce",
                )
            )

        limits = allocation_engine._calculate_limits_phase1(torrents)

        # All torrents should get at least minimum limit (10KB/s = 10240 B/s)
        for hash_, limit in limits.items():
            assert limit >= 10240

    @pytest.mark.asyncio
    async def test_apply_differential_updates_no_changes(self, allocation_engine):
        """Test differential updates when no changes are needed"""
        # Add torrent to cache
        success = allocation_engine.cache.add_torrent(
            "hash1", "tracker1", 100.0, 1000000
        )
        assert success, "Failed to add torrent to cache"

        # Verify torrent is in cache
        current_limit = allocation_engine.cache.get_current_limit("hash1")
        assert (
            current_limit == 1000000
        ), f"Current limit is {current_limit}, expected 1000000"

        # Mock needs_update to return False for this test case
        allocation_engine.qbit_client.needs_update.return_value = False

        # New limits same as current - no updates should be needed
        new_limits = {"hash1": 1000000}

        changes = await allocation_engine._apply_differential_updates(new_limits)

        assert changes == 0
        allocation_engine.qbit_client.set_torrents_upload_limits_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_differential_updates_with_changes(self, allocation_engine):
        """Test differential updates with actual changes"""
        allocation_engine.cache.add_torrent("hash1", "tracker1", 100.0, 1000000)

        # New limit different enough to trigger update
        new_limits = {"hash1": 2000000}  # Double the current limit

        changes = await allocation_engine._apply_differential_updates(new_limits)

        assert changes == 1
        allocation_engine.qbit_client.set_torrents_upload_limits_batch.assert_called_once()
        allocation_engine.rollback_manager.record_batch_changes.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_torrent_for_check(self, allocation_engine):
        """Test marking torrent for priority checking"""
        await allocation_engine.mark_torrent_for_check("hash123")

        assert "hash123" in allocation_engine.pending_checks

    @pytest.mark.asyncio
    async def test_schedule_tracker_update(self, allocation_engine):
        """Test scheduling tracker update"""
        await allocation_engine.schedule_tracker_update("http://tracker1.com/announce")

        assert "tracker1" in allocation_engine.pending_tracker_updates

    @pytest.mark.asyncio
    async def test_handle_torrent_deletion(self, allocation_engine):
        """Test handling torrent deletion"""
        # Add torrent to cache first
        allocation_engine.cache.add_torrent("hash1", "tracker1", 100.0, 1000)
        allocation_engine.pending_checks.add("hash1")

        await allocation_engine.handle_torrent_deletion("hash1")

        # Should be removed from cache and pending checks
        assert "hash1" not in allocation_engine.cache.hash_to_index
        assert "hash1" not in allocation_engine.pending_checks

    def test_update_rollout_percentage(self, allocation_engine):
        """Test updating rollout percentage"""
        allocation_engine.update_rollout_percentage(50)

        assert allocation_engine.gradual_rollout.rollout_percentage == 50
        assert allocation_engine.config.global_settings.rollout_percentage == 50

    def test_get_stats(self, allocation_engine):
        """Test getting basic statistics"""
        stats = allocation_engine.get_stats()

        assert "allocation_cycles" in stats
        assert "api_calls_last_cycle" in stats
        assert "active_torrents" in stats
        assert "rollout_percentage" in stats
        assert stats["rollout_percentage"] == 100

    def test_get_detailed_stats(self, allocation_engine):
        """Test getting detailed statistics"""
        stats = allocation_engine.get_detailed_stats()

        assert "allocation_cycles" in stats
        assert "cache_stats" in stats
        assert "estimated_memory_mb" in stats

    def test_get_tracker_stats(self, allocation_engine):
        """Test getting per-tracker statistics"""
        # Add some torrents to cache
        allocation_engine.cache.add_torrent("hash1", "tracker1", 1000000.0, 1000000)
        allocation_engine.cache.add_torrent("hash2", "tracker1", 500000.0, 1000000)
        allocation_engine.cache.add_torrent("hash3", "tracker2", 200000.0, 500000)

        # Mock tracker configs
        allocation_engine.tracker_matcher.get_all_tracker_configs.return_value = [
            Mock(
                id="tracker1", name="Tracker 1", max_upload_speed=5242880, priority=10
            ),
            Mock(id="tracker2", name="Tracker 2", max_upload_speed=2097152, priority=5),
        ]

        stats = allocation_engine.get_tracker_stats()

        assert "tracker1" in stats
        assert "tracker2" in stats

        # Check tracker1 stats
        tracker1_stats = stats["tracker1"]
        assert tracker1_stats["active_torrents"] == 2
        assert tracker1_stats["configured_limit_mbps"] == 5.0  # 5MB/s

        # Check current usage calculation
        expected_usage_mbps = (1000000.0 + 500000.0) / (1024 * 1024)
        assert abs(tracker1_stats["current_usage_mbps"] - expected_usage_mbps) < 0.01

    def test_specific_tracker_limit_over_default_unlimited(self, allocation_engine):
        """Specific tracker limit applies even if default is unlimited"""
        # Set default to unlimited and tracker1 finite
        allocation_engine.tracker_matcher.get_tracker_config.side_effect = (
            lambda tracker_id: Mock(
                id=tracker_id,
                max_upload_speed=(
                    -1
                    if tracker_id == "default"
                    else 5242880 if tracker_id == "tracker1" else 2097152
                ),
            )
        )

        torrent = TorrentInfo(
            hash="hspec1",
            name="t",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=50000,
            priority=1,
            num_seeds=1,
            num_leechs=1,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://tracker1.com/announce",
        )

        limits = allocation_engine._calculate_limits_phase1([torrent])
        assert limits[torrent.hash] == 5242880  # finite limit from tracker1, not -1

    def test_calculate_limits_unlimited_catch_all(self, allocation_engine):
        """Torrents mapped to catch-all get unlimited (-1) when default is -1"""
        # Make default tracker unlimited
        allocation_engine.tracker_matcher.get_tracker_config.side_effect = (
            lambda tracker_id: Mock(
                id=tracker_id,
                max_upload_speed=(
                    -1
                    if tracker_id == "default"
                    else 5242880 if tracker_id == "tracker1" else 2097152
                ),
            )
        )

        # Two torrents that map to default (no tracker1/2 in URL)
        torrents = [
            TorrentInfo(
                hash="hdef1",
                name="t1",
                state="uploading",
                progress=1.0,
                dlspeed=0,
                upspeed=50000,
                priority=1,
                num_seeds=1,
                num_leechs=1,
                ratio=1.0,
                size=1000,
                completed=1000,
                tracker="http://unknown.example/announce",
            ),
            TorrentInfo(
                hash="hdef2",
                name="t2",
                state="uploading",
                progress=1.0,
                dlspeed=0,
                upspeed=60000,
                priority=1,
                num_seeds=1,
                num_leechs=1,
                ratio=1.0,
                size=1000,
                completed=1000,
                tracker="udp://random.tracker/announce",
            ),
        ]

        limits = allocation_engine._calculate_limits_phase1(torrents)

        assert limits["hdef1"] == -1
        assert limits["hdef2"] == -1

    @pytest.mark.asyncio
    async def test_removes_cap_when_tracker_switched_to_unlimited(
        self, allocation_engine
    ):
        """Existing capped torrents become unlimited when tracker limit -> -1"""
        # One torrent on tracker1 with existing cap
        torrent = TorrentInfo(
            hash="hunlim1",
            name="t",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=75000,
            priority=1,
            num_seeds=1,
            num_leechs=1,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://tracker1.com/announce",
        )

        # Cache current state with a finite current limit
        allocation_engine.cache.add_torrent(
            torrent.hash, "tracker1", torrent.upspeed, 500000
        )

        # Change tracker1 to unlimited
        allocation_engine.tracker_matcher.get_tracker_config.side_effect = (
            lambda tracker_id: Mock(
                id=tracker_id,
                max_upload_speed=(
                    -1
                    if tracker_id == "tracker1"
                    else 2097152 if tracker_id == "tracker2" else 1048576
                ),
            )
        )

        # Calculate new limits under updated config
        new_limits = allocation_engine._calculate_limits_phase1([torrent])
        assert new_limits[torrent.hash] == -1

        # Apply differential updates; should switch to unlimited
        changes = await allocation_engine._apply_differential_updates(new_limits)

        assert changes == 1
        allocation_engine.qbit_client.set_torrents_upload_limits_batch.assert_called_once()
        # Cache should reflect unlimited after update
        assert allocation_engine.cache.get_current_limit(torrent.hash) == -1
