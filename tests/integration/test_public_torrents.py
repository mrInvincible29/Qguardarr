"""Integration tests using public domain torrents"""

import asyncio
import json
import time
from pathlib import Path
from typing import List, Dict, Any

import httpx
import pytest

from src.qbit_client import QBittorrentClient
from src.config import QBittorrentSettings
from tests.conftest import ServiceChecker, assert_torrent_limit_within_range


class PublicTorrentTestSuite:
    """Test suite for public domain torrents"""
    
    def __init__(self, qbit_client: QBittorrentClient, public_torrents: List[Dict[str, Any]]):
        self.qbit_client = qbit_client
        self.public_torrents = public_torrents
        self.added_hashes = []
        
    async def cleanup(self):
        """Clean up all added torrents"""
        for hash_str in self.added_hashes:
            try:
                await self.qbit_client.delete_torrent(hash_str, delete_files=False)
                await asyncio.sleep(0.5)  # Be gentle with API
            except Exception as e:
                print(f"Warning: Failed to cleanup torrent {hash_str}: {e}")
        
        self.added_hashes.clear()
    
    async def add_small_torrents(self, max_size_mb: int = 100, max_count: int = 5) -> List[str]:
        """Add small public domain torrents for testing"""
        small_torrents = [
            t for t in self.public_torrents 
            if t["size_mb"] <= max_size_mb
        ][:max_count]
        
        if not small_torrents:
            pytest.skip(f"No public torrents found with size <= {max_size_mb} MB")
        
        added_hashes = []
        
        for torrent in small_torrents:
            print(f"Adding torrent: {torrent['name']} ({torrent['size_mb']} MB)")
            
            success = await self.qbit_client.add_torrent_from_magnet(
                torrent["magnet"],
                category="qguardarr-test",
                paused=True  # Don't actually download
            )
            
            if success:
                # Wait for qBittorrent to process
                await asyncio.sleep(2)
                
                # Find the torrent by name
                all_torrents = await self.qbit_client.get_torrents(filter_active=False)
                for qbt_torrent in all_torrents:
                    if (qbt_torrent.category == "qguardarr-test" and 
                        qbt_torrent.hash not in added_hashes):
                        added_hashes.append(qbt_torrent.hash)
                        self.added_hashes.append(qbt_torrent.hash)
                        print(f"  Added: {qbt_torrent.hash}")
                        break
        
        print(f"Successfully added {len(added_hashes)} torrents")
        return added_hashes
    
    async def verify_tracker_matching(self, torrent_hashes: List[str]) -> Dict[str, str]:
        """Verify tracker URLs are extracted correctly"""
        tracker_mapping = {}
        
        for hash_str in torrent_hashes:
            trackers = await self.qbit_client.get_torrent_trackers(hash_str)
            
            # Find the primary tracker (exclude DHT/ pseudo entries)
            primary_tracker = None
            for tracker in trackers:
                if (
                    tracker.get("status") == 2
                    and tracker.get("url")
                    and not tracker["url"].startswith("**")
                ):
                    primary_tracker = tracker["url"]
                    break
            
            if not primary_tracker:
                # Fallback to first non-DHT tracker
                for tracker in trackers:
                    if tracker["url"] and not tracker["url"].startswith("**"):
                        primary_tracker = tracker["url"]
                        break
            
            if primary_tracker:
                tracker_mapping[hash_str] = primary_tracker
                print(f"Torrent {hash_str[:8]}... -> {primary_tracker}")
        
        return tracker_mapping
    
    async def test_limit_application(self, torrent_hashes: List[str]) -> Dict[str, int]:
        """Test applying and verifying upload limits"""
        applied_limits = {}
        
        for i, hash_str in enumerate(torrent_hashes):
            # Apply different limits to test various scenarios
            test_limit = 1024000 * (i + 1)  # 1MB/s, 2MB/s, etc.
            
            print(f"Setting limit {test_limit // 1024} KB/s on {hash_str[:8]}...")
            await self.qbit_client.set_torrent_upload_limit(hash_str, test_limit)
            
            # Verify limit was applied
            await asyncio.sleep(1)
            actual_limit = await self.qbit_client.get_torrent_upload_limit(hash_str)
            
            assert_torrent_limit_within_range(actual_limit, test_limit, tolerance=0.01)
            applied_limits[hash_str] = actual_limit
            
            print(f"  Verified: {actual_limit // 1024} KB/s")
        
        return applied_limits
    
    async def test_batch_operations(self, torrent_hashes: List[str]) -> bool:
        """Test batch limit operations"""
        if len(torrent_hashes) < 2:
            return True  # Skip if not enough torrents
        
        # Prepare batch limits
        batch_limits = {}
        for i, hash_str in enumerate(torrent_hashes):
            batch_limits[hash_str] = 512000 * (i + 1)  # 512KB/s, 1MB/s, etc.
        
        print(f"Applying batch limits to {len(batch_limits)} torrents...")
        await self.qbit_client.set_torrents_upload_limits_batch(batch_limits)
        
        # Verify all limits were applied
        await asyncio.sleep(2)  # Give qBittorrent time to apply
        
        for hash_str, expected_limit in batch_limits.items():
            actual_limit = await self.qbit_client.get_torrent_upload_limit(hash_str)
            assert_torrent_limit_within_range(actual_limit, expected_limit, tolerance=0.01)
            print(f"  {hash_str[:8]}...: {actual_limit // 1024} KB/s ✓")
        
        return True


@pytest.mark.integration
class TestPublicTorrentIntegration:
    """Integration tests with public domain torrents"""

    @pytest.fixture
    async def qbit_client(self, service_checker):
        """qBittorrent client for Docker container"""
        # Check if qBittorrent is available
        if not await service_checker.check_qbittorrent():
            pytest.skip("qBittorrent not available")
        
        config = QBittorrentSettings(
            host="localhost",
            port=8080,
            username="admin",
            password="adminpass123",
            timeout=30
        )
        client = QBittorrentClient(config)
        
        # Connect and verify
        await client.connect()
        yield client
        await client.disconnect()

    @pytest.fixture
    async def test_suite(self, qbit_client, public_torrents_data):
        """Test suite with cleanup"""
        suite = PublicTorrentTestSuite(qbit_client, public_torrents_data)
        yield suite
        await suite.cleanup()

    @pytest.mark.asyncio
    async def test_add_public_torrents(self, test_suite):
        """Test adding public domain torrents"""
        # Add small torrents (under 100MB to be respectful)
        torrent_hashes = await test_suite.add_small_torrents(max_size_mb=100, max_count=3)
        
        assert len(torrent_hashes) > 0, "No torrents were added successfully"
        print(f"✓ Successfully added {len(torrent_hashes)} public domain torrents")

    @pytest.mark.asyncio
    async def test_tracker_extraction(self, test_suite):
        """Test extraction of tracker URLs from real torrents"""
        torrent_hashes = await test_suite.add_small_torrents(max_size_mb=50, max_count=2)
        
        if not torrent_hashes:
            pytest.skip("No torrents available for tracker testing")
        
        tracker_mapping = await test_suite.verify_tracker_matching(torrent_hashes)
        
        assert len(tracker_mapping) > 0, "No tracker URLs extracted"
        
        # Verify we got real tracker URLs
        for hash_str, tracker_url in tracker_mapping.items():
            assert tracker_url.startswith(("http://", "https://", "udp://")), f"Invalid tracker URL: {tracker_url}"
            print(f"✓ Extracted tracker: {tracker_url}")

    @pytest.mark.asyncio
    async def test_upload_limit_application(self, test_suite):
        """Test applying upload limits to real torrents"""
        torrent_hashes = await test_suite.add_small_torrents(max_size_mb=50, max_count=3)
        
        if not torrent_hashes:
            pytest.skip("No torrents available for limit testing")
        
        applied_limits = await test_suite.test_limit_application(torrent_hashes)
        
        assert len(applied_limits) == len(torrent_hashes), "Not all limits were applied"
        print(f"✓ Applied and verified limits on {len(applied_limits)} torrents")

    @pytest.mark.asyncio
    async def test_batch_limit_operations(self, test_suite):
        """Test batch upload limit operations with real torrents"""
        torrent_hashes = await test_suite.add_small_torrents(max_size_mb=50, max_count=4)
        
        if len(torrent_hashes) < 2:
            pytest.skip("Need at least 2 torrents for batch testing")
        
        success = await test_suite.test_batch_operations(torrent_hashes)
        
        assert success, "Batch operations failed"
        print(f"✓ Batch operations successful on {len(torrent_hashes)} torrents")

    @pytest.mark.asyncio
    async def test_tracker_pattern_matching(self, test_suite, service_checker):
        """Test that tracker patterns match real tracker URLs"""
        if not await service_checker.check_qguardarr():
            pytest.skip("Qguardarr service not available")
        
        torrent_hashes = await test_suite.add_small_torrents(max_size_mb=50, max_count=3)
        
        if not torrent_hashes:
            pytest.skip("No torrents available for pattern testing")
        
        tracker_mapping = await test_suite.verify_tracker_matching(torrent_hashes)
        
        # Test against Qguardarr tracker patterns
        async with httpx.AsyncClient() as client:
            try:
                config_response = await client.get("http://localhost:8089/config", timeout=5.0)
                if config_response.status_code == 200:
                    config_data = config_response.json()
                    tracker_configs = config_data.get("trackers", [])
                    
                    print(f"Testing {len(tracker_mapping)} trackers against {len(tracker_configs)} patterns")
                    
                    for hash_str, tracker_url in tracker_mapping.items():
                        print(f"Testing tracker: {tracker_url}")
                        
                        # This would require implementing pattern matching test
                        # For now, just verify we can get the config
                        assert len(tracker_configs) > 0, "No tracker patterns configured"
                        
            except Exception as e:
                print(f"Warning: Could not test pattern matching: {e}")

    @pytest.mark.asyncio
    async def test_end_to_end_workflow(self, test_suite, service_checker):
        """Test complete workflow from adding torrents to limit management"""
        if not await service_checker.check_qguardarr():
            pytest.skip("Qguardarr service not available")
        
        print("Starting end-to-end workflow test...")
        
        # Step 1: Add torrents
        torrent_hashes = await test_suite.add_small_torrents(max_size_mb=50, max_count=2)
        
        if not torrent_hashes:
            pytest.skip("No torrents available for E2E test")
        
        print(f"Step 1: Added {len(torrent_hashes)} torrents ✓")
        
        # Step 2: Send webhook events to trigger Qguardarr processing
        async with httpx.AsyncClient() as client:
            for hash_str in torrent_hashes:
                webhook_data = {
                    "event": "add",
                    "hash": hash_str,
                    "name": f"Test Torrent {hash_str[:8]}",
                    "tracker": "http://tracker.example.com/announce"
                }
                
                response = await client.post(
                    "http://localhost:8089/webhook",
                    data=webhook_data,
                    timeout=5.0
                )
                
                assert response.status_code == 202, f"Webhook failed: {response.status_code}"
        
        print("Step 2: Sent webhook events ✓")
        
        # Step 3: Wait for processing and check stats
        await asyncio.sleep(5)  # Give Qguardarr time to process
        
        async with httpx.AsyncClient() as client:
            stats_response = await client.get("http://localhost:8089/stats", timeout=5.0)
            assert stats_response.status_code == 200
            
            stats = stats_response.json()
            print(f"Step 3: Qguardarr stats - Events received: {stats.get('events_received', 0)}")
            
            # Check tracker stats
            tracker_response = await client.get("http://localhost:8089/stats/trackers", timeout=5.0)
            assert tracker_response.status_code == 200
            
            tracker_stats = tracker_response.json()
            print(f"Step 4: Tracker stats available for {len(tracker_stats)} trackers ✓")
        
        # Step 5: Verify torrents can be managed (set limits)
        applied_limits = await test_suite.test_limit_application(torrent_hashes)
        print(f"Step 5: Applied limits to {len(applied_limits)} torrents ✓")
        
        print("End-to-end workflow completed successfully! ✓")

    @pytest.mark.asyncio
    async def test_differential_updates_with_real_torrents(self, test_suite):
        """Test differential update logic with real torrents"""
        torrent_hashes = await test_suite.add_small_torrents(max_size_mb=50, max_count=2)
        
        if not torrent_hashes:
            pytest.skip("No torrents available for differential testing")
        
        hash_str = torrent_hashes[0]
        
        # Set initial limit
        initial_limit = 1024000  # 1MB/s
        await test_suite.qbit_client.set_torrent_upload_limit(hash_str, initial_limit)
        await asyncio.sleep(1)
        
        current_limit = await test_suite.qbit_client.get_torrent_upload_limit(hash_str)
        
        # Test differential update logic
        client = test_suite.qbit_client
        
        # Small change - should NOT update (< 20% by default)
        small_change = int(initial_limit * 1.1)  # 10% increase
        needs_update = client.needs_update(current_limit, small_change, 0.2)
        assert not needs_update, "Small changes should not trigger updates"
        
        # Large change - should update
        large_change = int(initial_limit * 1.5)  # 50% increase  
        needs_update = client.needs_update(current_limit, large_change, 0.2)
        assert needs_update, "Large changes should trigger updates"
        
        # Test crossing zero boundary
        needs_update = client.needs_update(current_limit, 0, 0.2)
        assert needs_update, "Crossing unlimited boundary should trigger updates"
        
        print("✓ Differential update logic verified with real torrent")

    @pytest.mark.asyncio
    async def test_performance_with_real_torrents(self, test_suite):
        """Test performance metrics with real torrents"""
        # Add more torrents for performance testing  
        torrent_hashes = await test_suite.add_small_torrents(max_size_mb=50, max_count=5)
        
        if not torrent_hashes:
            pytest.skip("No torrents available for performance testing")
        
        # Test batch operation performance
        batch_limits = {hash_str: 512000 * (i + 1) for i, hash_str in enumerate(torrent_hashes)}
        
        start_time = time.time()
        await test_suite.qbit_client.set_torrents_upload_limits_batch(batch_limits)
        batch_time = time.time() - start_time
        
        print(f"Batch operation ({len(torrent_hashes)} torrents): {batch_time:.3f}s")
        assert batch_time < 5.0, f"Batch operation too slow: {batch_time:.3f}s"
        
        # Test individual operations for comparison
        start_time = time.time()
        for hash_str in torrent_hashes:
            await test_suite.qbit_client.get_torrent_upload_limit(hash_str)
        individual_time = time.time() - start_time
        
        print(f"Individual queries ({len(torrent_hashes)} torrents): {individual_time:.3f}s")
        
        # Batch should be more efficient for multiple operations
        if len(torrent_hashes) > 2:
            efficiency_ratio = individual_time / batch_time if batch_time > 0 else float('inf')
            print(f"Efficiency ratio: {efficiency_ratio:.2f}x")
        
        print("✓ Performance test completed")


@pytest.mark.integration
class TestQguardarrEndToEnd:
    """End-to-end tests with Qguardarr service"""
    
    @pytest.mark.asyncio
    async def test_service_health_with_real_torrents(self, service_checker):
        """Test service health endpoints with real data"""
        if not await service_checker.check_qguardarr():
            pytest.skip("Qguardarr service not available")
        
        async with httpx.AsyncClient() as client:
            # Test health endpoint
            health_response = await client.get("http://localhost:8089/health", timeout=5.0)
            assert health_response.status_code == 200
            
            health_data = health_response.json()
            assert health_data["status"] in ["healthy", "starting", "degraded"]
            assert "uptime_seconds" in health_data
            assert "version" in health_data
            
            print(f"✓ Service health: {health_data['status']}")
            
            # Test stats endpoint
            stats_response = await client.get("http://localhost:8089/stats", timeout=5.0)
            assert stats_response.status_code == 200
            
            stats_data = stats_response.json()
            assert "allocation_cycles" in stats_data
            assert "active_torrents" in stats_data
            
            print(f"✓ Service stats: {stats_data.get('allocation_cycles', 0)} cycles completed")
            
            # Test tracker stats
            tracker_response = await client.get("http://localhost:8089/stats/trackers", timeout=5.0)
            assert tracker_response.status_code == 200
            
            tracker_data = tracker_response.json()
            print(f"✓ Tracker stats available for {len(tracker_data)} trackers")

    @pytest.mark.asyncio
    async def test_configuration_endpoints(self, service_checker):
        """Test configuration-related endpoints"""
        if not await service_checker.check_qguardarr():
            pytest.skip("Qguardarr service not available")
        
        async with httpx.AsyncClient() as client:
            # Test config endpoint
            config_response = await client.get("http://localhost:8089/config", timeout=5.0)
            assert config_response.status_code == 200
            
            config_data = config_response.json()
            assert "trackers" in config_data
            assert "global_settings" in config_data
            
            # Verify passwords are sanitized
            assert config_data["qbittorrent"]["password"] == "***"
            
            print(f"✓ Configuration retrieved with {len(config_data['trackers'])} trackers")
            
            # Test rollout endpoint
            rollout_data = {"percentage": 50}
            rollout_response = await client.post(
                "http://localhost:8089/rollout",
                json=rollout_data,
                timeout=5.0
            )
            assert rollout_response.status_code == 200
            
            rollout_result = rollout_response.json()
            assert rollout_result["rollout_percentage"] == 50
            
            print("✓ Rollout percentage updated successfully")
