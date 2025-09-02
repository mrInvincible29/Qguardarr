"""Unit tests for webhook handler"""

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import Request

from src.config import CrossSeedSettings, GlobalSettings, QguardarrConfig
from src.webhook_handler import CrossSeedForwarder, WebhookEvent, WebhookHandler


class TestWebhookEvent:
    """Test WebhookEvent data structure"""

    def test_webhook_event_initialization(self):
        """Test WebhookEvent creation"""
        event_data = {
            "event": "complete",
            "hash": "abc123def456",
            "name": "Test Torrent",
            "tracker": "http://tracker.example.com/announce",
            "category": "movies",
            "tags": "tag1,tag2",
            "save_path": "/downloads",
            "timestamp": 1234567890.0,
        }

        event = WebhookEvent(event_data)

        assert event.event == "complete"
        assert event.hash == "abc123def456"
        assert event.name == "Test Torrent"
        assert event.tracker == "http://tracker.example.com/announce"
        assert event.category == "movies"
        assert event.tags == "tag1,tag2"
        assert event.save_path == "/downloads"
        assert event.timestamp == 1234567890.0

    def test_webhook_event_defaults(self):
        """Test WebhookEvent with minimal data"""
        event_data = {"event": "add", "hash": "abc123"}

        event = WebhookEvent(event_data)

        assert event.event == "add"
        assert event.hash == "abc123"
        assert event.name == ""
        assert event.tracker == ""
        assert event.category == ""
        assert event.tags == ""
        assert event.save_path == ""
        assert isinstance(event.timestamp, float)

    def test_webhook_event_to_dict(self):
        """Test converting WebhookEvent to dictionary"""
        event_data = {
            "event": "complete",
            "hash": "abc123",
            "name": "Test",
            "tracker": "http://test.com",
            "timestamp": 1234567890.0,
        }

        event = WebhookEvent(event_data)
        result_dict = event.to_dict()

        assert result_dict["event"] == "complete"
        assert result_dict["hash"] == "abc123"
        assert result_dict["name"] == "Test"
        assert result_dict["tracker"] == "http://test.com"
        assert result_dict["timestamp"] == 1234567890.0

    def test_webhook_event_str(self):
        """Test string representation"""
        event_data = {
            "event": "complete",
            "hash": "abc123def456",
            "name": "Very Long Torrent Name That Should Be Truncated",
        }

        event = WebhookEvent(event_data)
        str_repr = str(event)

        assert "complete" in str_repr
        assert "abc123de" in str_repr  # Truncated hash
        assert "Very Long Torrent Name That Sh" in str_repr  # Truncated name


class TestCrossSeedForwarder:
    """Test CrossSeedForwarder functionality"""

    @pytest.fixture
    def config_enabled(self, test_config):
        """Configuration with cross-seed enabled"""
        # Create a copy to avoid modifying shared fixture
        import copy

        config = copy.deepcopy(test_config)
        config.cross_seed.enabled = True
        config.cross_seed.url = "http://localhost:2468/api/webhook"
        config.cross_seed.api_key = "test-key"
        return config

    @pytest.fixture
    def config_disabled(self, test_config):
        """Configuration with cross-seed disabled"""
        # Create a copy to avoid modifying shared fixture
        import copy

        config = copy.deepcopy(test_config)
        config.cross_seed.enabled = False
        config.cross_seed.url = None
        config.cross_seed.api_key = None
        return config

    @pytest.mark.asyncio
    async def test_cross_seed_forwarder_disabled(self, config_disabled):
        """Test forwarder behavior when disabled"""
        forwarder = CrossSeedForwarder(config_disabled)
        await forwarder.start()

        # Should not create HTTP client
        assert forwarder.client is None

        event = WebhookEvent({"event": "complete", "hash": "test123"})
        result = await forwarder.forward_completion_event(event)

        # Should return True (success) even when disabled
        assert result is True

    @pytest.mark.asyncio
    async def test_cross_seed_forwarder_enabled(self, config_enabled):
        """Test forwarder behavior when enabled"""
        forwarder = CrossSeedForwarder(config_enabled)
        await forwarder.start()

        # Should create HTTP client
        assert forwarder.client is not None

        await forwarder.stop()
        assert forwarder.client is None

    @pytest.mark.asyncio
    async def test_forward_completion_event_non_complete(self, config_enabled):
        """Test that only completion events are forwarded"""
        forwarder = CrossSeedForwarder(config_enabled)
        await forwarder.start()

        try:
            # Non-completion event should return True without forwarding
            event = WebhookEvent({"event": "add", "hash": "test123"})
            result = await forwarder.forward_completion_event(event)

            assert result is True
        finally:
            await forwarder.stop()

    @pytest.mark.asyncio
    async def test_forward_completion_event_success(self, config_enabled):
        """Test successful completion event forwarding"""
        forwarder = CrossSeedForwarder(config_enabled)

        # Mock the HTTP client
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        forwarder.client = mock_client

        event = WebhookEvent(
            {
                "event": "complete",
                "hash": "test123",
                "name": "Test Torrent",
                "category": "movies",
                "tags": "tag1,tag2",
                "save_path": "/downloads",
            }
        )

        result = await forwarder.forward_completion_event(event)

        assert result is True

        # Verify HTTP call was made
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args

        assert call_args[0][0] == "http://localhost:2468/api/webhook"

        # Check payload
        payload = call_args[1]["json"]
        assert payload["infoHash"] == "test123"
        assert payload["name"] == "Test Torrent"
        assert payload["category"] == "movies"

        # Check headers
        headers = call_args[1]["headers"]
        assert headers["X-API-Key"] == "test-key"

        # Check stats
        assert forwarder.stats["forwarded"] == 1

    @pytest.mark.asyncio
    async def test_forward_completion_event_failure(self, config_enabled):
        """Test handling of forwarding failures"""
        forwarder = CrossSeedForwarder(config_enabled)

        # Mock the HTTP client to raise exception
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Network error")

        forwarder.client = mock_client

        event = WebhookEvent({"event": "complete", "hash": "test123"})

        result = await forwarder.forward_completion_event(event)

        assert result is False
        assert forwarder.stats["failed"] == 1
        assert "Network error" in forwarder.stats["last_error"]

    @pytest.mark.asyncio
    async def test_forward_with_retry(self, config_enabled):
        """Test forwarding with retry logic"""
        forwarder = CrossSeedForwarder(config_enabled)

        # Mock client that fails twice then succeeds
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary failure")
            return mock_response

        mock_client.post.side_effect = mock_post
        forwarder.client = mock_client

        event = WebhookEvent({"event": "complete", "hash": "test123"})

        # Should eventually succeed after retries
        result = await forwarder.forward_with_retry(event, max_retries=3)

        assert result is True
        assert mock_client.post.call_count == 3

    def test_get_stats(self, config_enabled):
        """Test getting forwarder statistics"""
        forwarder = CrossSeedForwarder(config_enabled)

        # Modify stats
        forwarder.stats["forwarded"] = 5
        forwarder.stats["failed"] = 2
        forwarder.stats["last_error"] = "Test error"

        stats = forwarder.get_stats()

        assert stats["forwarded"] == 5
        assert stats["failed"] == 2
        assert stats["last_error"] == "Test error"

        # Should be a copy, not the original
        stats["forwarded"] = 10
        assert forwarder.stats["forwarded"] == 5


class TestWebhookHandler:
    """Test WebhookHandler functionality"""

    @pytest.fixture
    def config(self, test_config):
        """Test configuration"""
        # Create a copy to avoid modifying shared fixture
        import copy

        config = copy.deepcopy(test_config)
        config.cross_seed.enabled = False
        config.cross_seed.url = None
        config.cross_seed.api_key = None
        return config

    @pytest.fixture
    def mock_allocation_engine(self):
        """Mock allocation engine"""
        return AsyncMock()

    @pytest.fixture
    def webhook_handler(self, config, mock_allocation_engine):
        """WebhookHandler instance"""
        return WebhookHandler(config, mock_allocation_engine)

    @pytest.mark.asyncio
    async def test_webhook_handler_initialization(self, webhook_handler):
        """Test webhook handler initialization"""
        assert webhook_handler.event_queue.maxsize == 1000
        assert webhook_handler._running is False
        assert webhook_handler.stats["events_received"] == 0

    @pytest.mark.asyncio
    async def test_handle_webhook_success(self, webhook_handler):
        """Test successful webhook handling"""
        # Mock FastAPI request
        mock_request = AsyncMock(spec=Request)
        mock_form_data = {
            "event": "complete",
            "hash": "test123",
            "name": "Test Torrent",
            "tracker": "http://example.com",
        }
        # form() is async, so it should return an awaitable
        mock_request.form = AsyncMock(return_value=mock_form_data)

        start_time = time.time()
        response = await webhook_handler.handle_webhook(mock_request)
        processing_time = (time.time() - start_time) * 1000

        # Should be very fast
        assert processing_time < 50  # Less than 50ms

        # Check response
        assert response.status_code == 202
        response_data = eval(response.body.decode())  # JSON response
        assert response_data["status"] == "queued"
        assert response_data["queue_size"] >= 0

        # Check stats
        assert webhook_handler.stats["events_received"] == 1
        assert webhook_handler.event_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_handle_webhook_queue_full(self, webhook_handler):
        """Test webhook handling when queue is full"""
        # Fill up the queue
        for i in range(1000):  # Queue maxsize is 1000
            await webhook_handler.event_queue.put(f"event{i}")

        # Mock request
        mock_request = AsyncMock(spec=Request)
        mock_request.form = AsyncMock(
            return_value={"event": "complete", "hash": "test123"}
        )

        response = await webhook_handler.handle_webhook(mock_request)

        # Should still return success but drop the event
        assert response.status_code == 202
        assert webhook_handler.stats["events_dropped"] == 1

    @pytest.mark.asyncio
    async def test_handle_webhook_parsing_error(self, webhook_handler):
        """Test webhook handling with parsing errors"""
        # Mock request that throws exception
        mock_request = AsyncMock(spec=Request)
        mock_request.form.side_effect = Exception("Parsing failed")

        response = await webhook_handler.handle_webhook(mock_request)

        # Should still return 202 to avoid qBittorrent retries
        assert response.status_code == 202
        response_data = eval(response.body.decode())
        assert response_data["status"] == "error"

    @pytest.mark.asyncio
    async def test_start_event_processor(self, webhook_handler):
        """Test starting the event processor"""
        await webhook_handler.start_event_processor()

        assert webhook_handler._running is True
        assert webhook_handler._processing_task is not None

        # Clean up
        await webhook_handler.stop()

    @pytest.mark.asyncio
    async def test_stop_event_processor(self, webhook_handler):
        """Test stopping the event processor"""
        await webhook_handler.start_event_processor()

        assert webhook_handler._running is True

        await webhook_handler.stop()

        assert webhook_handler._running is False

    @pytest.mark.asyncio
    async def test_process_completion_event(self, webhook_handler):
        """Test processing completion event"""
        await webhook_handler.start_event_processor()

        try:
            event = WebhookEvent(
                {"event": "complete", "hash": "test123", "name": "Test Torrent"}
            )

            await webhook_handler._process_event_safely(event)

            # Should forward to cross-seed and mark for check
            webhook_handler.allocation_engine.mark_torrent_for_check.assert_called_with(
                "test123"
            )

        finally:
            await webhook_handler.stop()

    @pytest.mark.asyncio
    async def test_process_add_event(self, webhook_handler):
        """Test processing add event"""
        await webhook_handler.start_event_processor()

        try:
            event = WebhookEvent(
                {
                    "event": "add",
                    "hash": "test123",
                    "tracker": "http://example.com/announce",
                }
            )

            await webhook_handler._process_event_safely(event)

            # Should mark for check and schedule tracker update
            webhook_handler.allocation_engine.mark_torrent_for_check.assert_called_with(
                "test123"
            )
            webhook_handler.allocation_engine.schedule_tracker_update.assert_called_with(
                "http://example.com/announce"
            )

        finally:
            await webhook_handler.stop()

    @pytest.mark.asyncio
    async def test_process_delete_event(self, webhook_handler):
        """Test processing delete event"""
        await webhook_handler.start_event_processor()

        try:
            event = WebhookEvent({"event": "delete", "hash": "test123"})

            await webhook_handler._process_event_safely(event)

            # Should handle torrent deletion
            webhook_handler.allocation_engine.handle_torrent_deletion.assert_called_with(
                "test123"
            )

        finally:
            await webhook_handler.stop()

    @pytest.mark.asyncio
    async def test_process_event_error_handling(self, webhook_handler):
        """Test error handling in event processing"""
        await webhook_handler.start_event_processor()

        try:
            # Mock allocation engine to raise exception
            webhook_handler.allocation_engine.mark_torrent_for_check.side_effect = (
                Exception("Test error")
            )

            event = WebhookEvent({"event": "complete", "hash": "test123"})

            # Should not raise exception even if processing fails
            await webhook_handler._process_event_safely(event)

            assert webhook_handler.stats["processing_errors"] == 1

        finally:
            await webhook_handler.stop()

    def test_get_queue_stats(self, webhook_handler):
        """Test getting queue statistics"""
        # Modify some stats
        webhook_handler.stats["events_received"] = 10
        webhook_handler.stats["events_processed"] = 8
        webhook_handler.stats["events_dropped"] = 1
        webhook_handler.stats["processing_errors"] = 1

        stats = webhook_handler.get_queue_stats()

        assert stats["events_received"] == 10
        assert stats["events_processed"] == 8
        assert stats["events_dropped"] == 1
        assert stats["processing_errors"] == 1
        assert "queue_size" in stats
        assert "processing_rate" in stats

    def test_calculate_processing_rate(self, webhook_handler):
        """Test processing rate calculation"""
        # No events processed
        rate = webhook_handler._calculate_processing_rate()
        assert rate == 0.0

        # Mock some processed events
        webhook_handler.stats["events_processed"] = 10
        webhook_handler.stats["last_event_time"] = time.time() - 2.0  # 2 seconds ago

        rate = webhook_handler._calculate_processing_rate()
        # Should be around 5 events per second (10 events / 2 seconds)
        assert 4.0 <= rate <= 6.0

    @pytest.mark.asyncio
    async def test_drain_queue_success(self, webhook_handler):
        """Test successful queue draining"""
        # Start processor
        await webhook_handler.start_event_processor()

        try:
            # Add an event
            await webhook_handler.event_queue.put({"event": "test"})

            # Drain should succeed quickly since there's only one event
            result = await webhook_handler.drain_queue(timeout=1.0)
            assert result is True

        finally:
            await webhook_handler.stop()

    @pytest.mark.asyncio
    async def test_drain_queue_timeout(self, webhook_handler):
        """Test queue draining timeout"""
        # Don't start processor, so events won't be processed

        # Add events that won't be processed
        await webhook_handler.event_queue.put({"event": "test1"})
        await webhook_handler.event_queue.put({"event": "test2"})

        # Drain should timeout
        result = await webhook_handler.drain_queue(timeout=0.1)
        assert result is False
