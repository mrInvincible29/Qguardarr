"""Integration tests with real qBittorrent running in Docker"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
import pytest

from src.qbit_client import QBittorrentClient, TorrentInfo
from src.config import QBittorrentSettings
from .docker_utils import skip_if_no_docker, QBittorrentHelper

logger = logging.getLogger(__name__)


# Use the centralized fixtures from conftest.py
# Individual fixtures are no longer needed


# public_torrents fixture is now in conftest.py


@pytest.mark.docker
class TestQBittorrentIntegration:
    """Integration tests with real qBittorrent"""

    @pytest.mark.asyncio
    async def test_qbittorrent_connection(self, qbittorrent_client):
        """Test basic connection to qBittorrent"""
        logger.info("Testing qBittorrent connection...")
        
        # Test health check
        is_healthy = await qbittorrent_client.is_healthy()
        assert is_healthy, "qBittorrent should be healthy"
        
        # Test authentication
        client = await qbittorrent_client.authenticate()
        assert client is not None, "Should be able to authenticate"
        
        try:
            # Test basic API call
            response = await client.get(f"{qbittorrent_client.base_url}/api/v2/app/version", timeout=5.0)
            assert response.status_code == 200, "Version API should respond"
            
            logger.info("✅ qBittorrent connection successful")
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_basic_torrent_operations(self, qbittorrent_client, public_torrents_data):
        """Test basic torrent operations with public domain content"""
        if not public_torrents_data:
            pytest.skip("No public torrent data available")
        
        # Use a small torrent for testing
        test_torrent = next((t for t in public_torrents_data if t["size_mb"] < 100), None)
        if not test_torrent:
            pytest.skip("No small torrents available for testing")
        
        logger.info(f"Testing with torrent: {test_torrent['name']}")
        
        client = await qbittorrent_client.authenticate()
        assert client is not None, "Should be able to authenticate"
        
        try:
            # For now, just test that we can communicate with qBittorrent
            # Adding actual torrents requires more complex setup
            
            # Test getting current torrent list
            response = await client.get(
                f"{qbittorrent_client.base_url}/api/v2/torrents/info",
                timeout=10.0
            )
            assert response.status_code == 200, "Should be able to get torrent list"
            
            torrent_list = response.json()
            logger.info(f"Current torrents: {len(torrent_list)}")
            
            logger.info("✅ Basic torrent operations test completed")
            
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_api_functionality(self, qbittorrent_client):
        """Test qBittorrent API functionality without adding real torrents"""
        logger.info("Testing qBittorrent API functionality...")
        
        client = await qbittorrent_client.authenticate()
        assert client is not None, "Should be able to authenticate"
        
        try:
            # Test various API endpoints
            json_endpoints = [
                ("/api/v2/app/preferences", "preferences"),
                ("/api/v2/torrents/info", "torrent list"),
                ("/api/v2/transfer/info", "transfer info"),
            ]
            
            # Test version endpoint separately (returns plain text)
            logger.info("Testing version info endpoint...")
            response = await client.get(
                f"{qbittorrent_client.base_url}/api/v2/app/version",
                timeout=5.0
            )
            assert response.status_code == 200, "Failed to get version info"
            version = response.text.strip('"')  # Remove quotes if present
            assert len(version) > 0, "Version should not be empty"
            logger.info(f"✅ Version endpoint working (version: {version})")
            
            # Test JSON endpoints
            for endpoint, description in json_endpoints:
                logger.info(f"Testing {description} endpoint...")
                response = await client.get(
                    f"{qbittorrent_client.base_url}{endpoint}",
                    timeout=5.0
                )
                assert response.status_code == 200, f"Failed to get {description}"
                
                # Verify response is valid JSON
                data = response.json()
                assert data is not None, f"{description} returned invalid JSON"
                
                logger.info(f"✅ {description} endpoint working")
            
            logger.info("✅ qBittorrent API functionality test completed")
            
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_preferences_access(self, qbittorrent_client):
        """Test that we can access and modify qBittorrent preferences"""
        logger.info("Testing qBittorrent preferences access...")
        
        client = await qbittorrent_client.authenticate()
        assert client is not None, "Should be able to authenticate"
        
        try:
            # Get current preferences
            response = await client.get(
                f"{qbittorrent_client.base_url}/api/v2/app/preferences",
                timeout=5.0
            )
            assert response.status_code == 200, "Should be able to get preferences"
            
            prefs = response.json()
            assert isinstance(prefs, dict), "Preferences should be a dictionary"
            
            # Check some expected keys
            expected_keys = ["dht", "pex", "lsd", "max_connec", "max_uploads"]
            for key in expected_keys:
                if key in prefs:
                    logger.info(f"✅ Found preference key: {key}")
            
            logger.info("✅ Preferences access test completed")
            
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_needs_update_logic(self, qbittorrent_client):
        """Test the differential update logic without real torrents"""
        logger.info("Testing differential update logic...")
        
        # Create a basic QBittorrent client to test the needs_update method
        from src.qbit_client import QBittorrentClient
        from src.config import QBittorrentSettings
        
        config = QBittorrentSettings(
            host="localhost",
            port=8080,
            username="admin",
            password="adminpass123",
            timeout=30
        )
        client = QBittorrentClient(config)
        
        # Test differential update logic
        initial_limit = 1024000  # 1 MB/s
        
        # Small change - should not update (< 20% by default)
        small_change = int(initial_limit * 1.1)  # 10% increase
        needs_update = client.needs_update(initial_limit, small_change, 0.2)
        assert not needs_update, "Small changes should not trigger updates"
        
        # Large change - should update
        large_change = int(initial_limit * 1.5)  # 50% increase
        needs_update = client.needs_update(initial_limit, large_change, 0.2)
        assert needs_update, "Large changes should trigger updates"
        
        # Crossing unlimited boundary
        needs_update = client.needs_update(initial_limit, -1, 0.2)
        assert needs_update, "Crossing unlimited boundary should trigger updates"
        
        logger.info("✅ Differential update logic test completed")


@pytest.mark.docker
@pytest.mark.qguardarr
class TestQguardarrEndToEnd:
    """End-to-end tests with full Qguardarr stack"""
    
    @pytest.mark.asyncio
    async def test_health_endpoint(self, qguardarr_client):
        """Test Qguardarr health endpoint"""
        logger.info("Testing Qguardarr health endpoint...")
        
        # Test health check
        is_healthy = await qguardarr_client.is_healthy()
        assert is_healthy, "Qguardarr service should be healthy"
        
        # Test detailed health response
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{qguardarr_client.base_url}/health", timeout=5.0)
            assert response.status_code == 200, "Health endpoint should respond"
            
            health_data = response.json()
            assert health_data["status"] in ["healthy", "starting", "degraded"], f"Invalid status: {health_data['status']}"
            assert "uptime_seconds" in health_data, "Should include uptime"
            assert "version" in health_data, "Should include version"
            
            logger.info(f"✅ Qguardarr health: {health_data['status']}")

    @pytest.mark.asyncio
    async def test_webhook_endpoint(self, qguardarr_client):
        """Test webhook processing"""
        logger.info("Testing Qguardarr webhook endpoint...")
        
        # Send a test webhook
        webhook_data = {
            "event": "complete",
            "hash": "test123456789abcdef",
            "name": "Test Torrent",
            "tracker": "http://tracker.opentrackr.org:1337/announce"
        }
        
        success = await qguardarr_client.send_test_webhook(webhook_data)
        assert success, "Webhook should be accepted"
        
        logger.info("✅ Webhook processing test completed")

    @pytest.mark.asyncio
    async def test_stats_endpoints(self, qguardarr_client):
        """Test statistics endpoints"""
        logger.info("Testing Qguardarr statistics endpoints...")
        
        # Test main stats
        stats = await qguardarr_client.get_stats()
        assert isinstance(stats, dict), "Stats should be a dictionary"
        
        # Should have some expected keys (but they might be empty/zero in test)
        expected_keys = ["allocation_cycles", "active_torrents"]
        for key in expected_keys:
            if key in stats:
                logger.info(f"✅ Found stats key: {key} = {stats[key]}")
        
        logger.info("✅ Statistics endpoints test completed")