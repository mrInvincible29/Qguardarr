"""Integration tests for SLA compliance requirements"""

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from src.allocation import AllocationEngine
from src.webhook_handler import WebhookEvent, WebhookHandler


@pytest.mark.integration
class TestSLACompliance:
    """Test SLA requirements for Phase 1"""

    @pytest.mark.asyncio
    async def test_new_torrent_gets_limit_within_2_minutes(self, integration_config):
        """
        Test that new torrents receive upload limits within 2 minutes

        This is a critical Phase 1 requirement - new torrents must get
        default limits within 2 minutes via webhook events or polling
        """
        # Mock components for controlled testing
        mock_qbit_client = AsyncMock()
        mock_tracker_matcher = AsyncMock()
        mock_rollback_manager = AsyncMock()

        # Configure mocks
        from unittest.mock import Mock

        mock_tracker_matcher.match_tracker.return_value = "archive_org"
        mock_qbit_client.needs_update = Mock(
            return_value=True
        )  # Sync mock for sync method
        mock_qbit_client.set_torrents_upload_limits_batch.return_value = None
        mock_rollback_manager.record_batch_changes.return_value = None

        # Create allocation engine
        allocation_engine = AllocationEngine(
            config=integration_config,
            qbit_client=mock_qbit_client,
            tracker_matcher=mock_tracker_matcher,
            rollback_manager=mock_rollback_manager,
        )

        # Test scenario: Simplified test for SLA compliance
        # This focuses on the core requirement: torrent limits applied within 2 minutes

        test_hash = "1234567890abcdef1234567890abcdef12345678"

        start_time = time.time()

        # Simulate new torrent being added to allocation engine
        # This is what would happen after webhook processing
        allocation_engine.cache.add_torrent(
            torrent_hash=test_hash,
            tracker_id="archive_org",
            upload_speed=50.0 * 1024,  # 50 KB/s
            current_limit=-1,  # No limit initially
        )

        # Calculate new limit based on tracker config (5 MB/s for archive_org)
        new_limits = {test_hash: 5 * 1024 * 1024}

        # Apply the limits - this should complete within 2 minutes (well under)
        await allocation_engine._apply_differential_updates(new_limits)

        processing_time = time.time() - start_time

        # Verify SLA compliance
        assert processing_time < 120, (
            f"Torrent limit application took {processing_time:.1f}s, "
            f"exceeds 2-minute SLA ({120}s)"
        )

        # Verify the torrent was processed
        mock_qbit_client.set_torrents_upload_limits_batch.assert_called_once()
        mock_rollback_manager.record_batch_changes.assert_called_once()

        # In real conditions this should be very fast
        assert (
            processing_time < 1.0
        ), f"Processing took {processing_time:.3f}s, should be < 1s in test conditions"

        print(f"✅ New torrent limit applied in {processing_time:.3f}s (SLA: <120s)")

    @pytest.mark.asyncio
    async def test_webhook_response_time_sla(self, integration_config):
        """
        Test that webhook responses are returned within 10ms to prevent qBittorrent timeouts

        This is critical for preventing qBittorrent from timing out webhook calls
        """
        # Create webhook handler with mock allocation engine
        mock_allocation_engine = AsyncMock()
        webhook_handler = WebhookHandler(
            config=integration_config, allocation_engine=mock_allocation_engine
        )

        # Mock FastAPI request
        mock_request = AsyncMock()
        mock_request.form = AsyncMock(
            return_value={
                "event": "complete",
                "hash": "test123456789abcdef",
                "name": "Test Torrent",
                "tracker": "http://test1.com/announce",
            }
        )

        # Measure webhook response time
        start_time = time.time()
        response = await webhook_handler.handle_webhook(mock_request)
        response_time_ms = (time.time() - start_time) * 1000

        # Verify SLA compliance
        assert response_time_ms < 10, (
            f"Webhook response took {response_time_ms:.1f}ms, "
            f"exceeds 10ms SLA for qBittorrent compatibility"
        )

        # Verify successful response
        assert response.status_code == 202

        print(f"✅ Webhook responded in {response_time_ms:.1f}ms (SLA: <10ms)")

    @pytest.mark.asyncio
    async def test_allocation_cycle_performance_sla(self, integration_config):
        """
        Test that allocation cycles complete within 10 seconds for active torrents

        This ensures the system can keep up with torrent state changes
        """
        # Mock components
        mock_qbit_client = AsyncMock()
        mock_tracker_matcher = AsyncMock()
        mock_rollback_manager = AsyncMock()

        # Configure mocks for realistic scenario
        mock_tracker_matcher.match_tracker.return_value = "test_tracker1"
        mock_qbit_client.needs_update.return_value = True
        mock_qbit_client.set_torrents_upload_limits_batch.return_value = None
        mock_rollback_manager.record_batch_changes.return_value = None

        allocation_engine = AllocationEngine(
            config=integration_config,
            qbit_client=mock_qbit_client,
            tracker_matcher=mock_tracker_matcher,
            rollback_manager=mock_rollback_manager,
        )

        # Simulate 500 active torrents (realistic Phase 1 load)
        test_torrents = {}
        for i in range(500):
            torrent_hash = f"{i:08x}" + "a" * 32
            test_torrents[torrent_hash] = {
                "tracker": "http://test1.com/announce",
                "upload_speed": 1024 * (i % 100),  # Varying speeds
                "current_limit": 1048576,  # 1 MB/s
            }

        # Calculate new limits for all torrents
        tracker_limit = 5 * 1024 * 1024  # 5 MB/s for test_tracker1
        limit_per_torrent = tracker_limit // len(test_torrents)

        new_limits = {hash: limit_per_torrent for hash in test_torrents.keys()}

        # Measure allocation cycle time
        start_time = time.time()
        changes = await allocation_engine._apply_differential_updates(new_limits)
        cycle_time = time.time() - start_time

        # Verify SLA compliance
        assert cycle_time < 10, (
            f"Allocation cycle took {cycle_time:.1f}s for {len(test_torrents)} torrents, "
            f"exceeds 10s SLA"
        )

        # Verify processing occurred
        assert changes > 0, "No limit changes were applied"
        mock_qbit_client.set_torrents_upload_limits_batch.assert_called_once()

        print(
            f"✅ Allocation cycle for {len(test_torrents)} torrents completed in {cycle_time:.3f}s (SLA: <10s)"
        )

    @pytest.mark.asyncio
    async def test_memory_usage_sla(self, integration_config):
        """
        Test that memory usage stays under 60MB for realistic torrent loads

        This ensures the system can run on resource-constrained servers
        """
        import gc

        import psutil

        # Force garbage collection before measuring
        gc.collect()

        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Mock components
        mock_qbit_client = AsyncMock()
        mock_tracker_matcher = AsyncMock()
        mock_rollback_manager = AsyncMock()

        allocation_engine = AllocationEngine(
            config=integration_config,
            qbit_client=mock_qbit_client,
            tracker_matcher=mock_tracker_matcher,
            rollback_manager=mock_rollback_manager,
        )

        # Simulate realistic cache usage - 500 active torrents
        for i in range(500):
            torrent_hash = f"{i:08x}" + "b" * 32
            success = allocation_engine.cache.add_torrent(
                torrent_hash=torrent_hash,
                tracker_id="test_tracker1",
                upload_speed=float(1024 * (i % 100)),
                current_limit=1048576,
            )
            assert success, f"Failed to add torrent {i} to cache"

        # Force garbage collection and measure memory
        gc.collect()

        current_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_used = current_memory - initial_memory

        # Verify SLA compliance - Phase 1 target is <60MB for 500 torrents
        assert (
            memory_used < 60
        ), f"Memory usage {memory_used:.1f}MB for 500 torrents exceeds 60MB SLA"

        # Verify cache functionality
        assert allocation_engine.cache.used_count == 500

        print(f"✅ Memory usage: {memory_used:.1f}MB for 500 torrents (SLA: <60MB)")
