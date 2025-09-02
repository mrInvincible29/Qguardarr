"""Webhook handler with queue pattern and cross-seed forwarding"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Union

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from src.config import QguardarrConfig


class WebhookEvent:
    """Webhook event data structure"""

    def __init__(self, event_data: Dict[str, Any]):
        self.event = event_data.get("event", "")
        self.hash = event_data.get("hash", "")
        self.name = event_data.get("name", "")
        self.tracker = event_data.get("tracker", "")
        self.timestamp = event_data.get("timestamp", time.time())

        # Additional fields for different event types
        self.category = event_data.get("category", "")
        self.tags = event_data.get("tags", "")
        self.save_path = event_data.get("save_path", "")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "event": self.event,
            "hash": self.hash,
            "name": self.name,
            "tracker": self.tracker,
            "timestamp": self.timestamp,
            "category": self.category,
            "tags": self.tags,
            "save_path": self.save_path,
        }

    def __str__(self) -> str:
        return (
            f"WebhookEvent(event={self.event}, hash={self.hash[:8]}..., "
            f"name={self.name[:30]}...)"
        )


class CrossSeedForwarder:
    """Handles forwarding completion events to cross-seed"""

    def __init__(self, config: QguardarrConfig):
        self.config = config
        self.client: Optional[httpx.AsyncClient] = None
        self.stats: Dict[str, Union[int, str, None]] = {
            "forwarded": 0,
            "failed": 0,
            "last_error": None,
        }

    async def start(self):
        """Initialize HTTP client"""
        if not self.config.cross_seed.enabled:
            logging.info("Cross-seed forwarding disabled")
            return

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.cross_seed.timeout),
            limits=httpx.Limits(max_connections=5),
        )
        logging.info("Cross-seed forwarder initialized")

    async def stop(self):
        """Clean up HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None

    async def forward_completion_event(self, event: WebhookEvent) -> bool:
        """
        Forward completion event to cross-seed

        Args:
            event: Webhook event to forward

        Returns:
            True if forwarded successfully, False otherwise
        """
        if not self.config.cross_seed.enabled or not self.client:
            return True  # Consider success if not enabled

        if event.event != "complete":
            return True  # Only forward completion events

        if not self.config.cross_seed.url:
            return True  # No URL configured

        try:
            payload = {
                "infoHash": event.hash,
                "name": event.name,
                "category": event.category,
                "tags": event.tags,
                "savePath": event.save_path,
            }

            headers = {}
            if self.config.cross_seed.api_key:
                headers["X-API-Key"] = self.config.cross_seed.api_key

            # MyPy assertion - we already checked above
            assert self.config.cross_seed.url is not None
            response = await self.client.post(
                self.config.cross_seed.url, json=payload, headers=headers
            )
            response.raise_for_status()

            self.stats["forwarded"] = (self.stats["forwarded"] or 0) + 1
            logging.debug(f"Cross-seed forwarded: {event.hash}")
            return True

        except Exception as e:
            self.stats["failed"] = (self.stats["failed"] or 0) + 1
            self.stats["last_error"] = str(e)
            logging.warning(f"Cross-seed forward failed for {event.hash}: {e}")
            return False

    async def forward_with_retry(
        self, event: WebhookEvent, max_retries: int = 3
    ) -> bool:
        """Forward with exponential backoff retry"""
        for attempt in range(max_retries):
            if await self.forward_completion_event(event):
                return True

            if attempt < max_retries - 1:
                delay = 2**attempt  # Exponential backoff
                await asyncio.sleep(delay)

        return False

    def get_stats(self) -> Dict[str, Any]:
        """Get forwarding statistics"""
        return self.stats.copy()


class WebhookHandler:
    """
    Webhook handler with queue pattern to ensure <10ms response time
    """

    def __init__(self, config: QguardarrConfig, allocation_engine):
        self.config = config
        self.allocation_engine = allocation_engine
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.cross_seed_forwarder = CrossSeedForwarder(config)

        self.stats: Dict[str, Union[int, float, None]] = {
            "events_received": 0,
            "events_processed": 0,
            "events_dropped": 0,
            "processing_errors": 0,
            "last_event_time": None,
            "queue_size": 0,
        }

        self._processing_task: Optional[asyncio.Task] = None
        self._running = False

    async def handle_webhook(self, request: Request) -> JSONResponse:
        """
        Handle incoming webhook with minimal processing time (<10ms)

        This method ONLY parses and queues the event - no processing!
        """
        start_time = time.time()

        try:
            # Parse form data quickly
            form_data = await request.form()

            # Create minimal event object
            event_data = {
                "event": form_data.get("event", ""),
                "hash": form_data.get("hash", ""),
                "name": form_data.get("name", ""),
                "tracker": form_data.get("tracker", ""),
                "category": form_data.get("category", ""),
                "tags": form_data.get("tags", ""),
                "save_path": form_data.get("save_path", ""),
                "timestamp": time.time(),
            }

            # Queue without blocking - fail fast if queue full
            try:
                self.event_queue.put_nowait(event_data)
                self.stats["events_received"] = (self.stats["events_received"] or 0) + 1
                self.stats["last_event_time"] = time.time()
                self.stats["queue_size"] = self.event_queue.qsize()

                # Convert to ms
                processing_time = (time.time() - start_time) * 1000
                logging.debug(
                    f"Webhook queued in {processing_time:.1f}ms: "
                    f"{event_data.get('event')} - "
                    f"{event_data.get('hash', '')[:8]}"
                )

            except asyncio.QueueFull:
                self.stats["events_dropped"] = (self.stats["events_dropped"] or 0) + 1
                logging.warning("Event queue full, dropping event")

            # Return immediately to qBittorrent (should be <10ms total)
            return JSONResponse(
                {
                    "status": "queued",
                    "queue_size": self.event_queue.qsize(),
                    "processing_time_ms": round(
                        (time.time() - start_time) * 1000, 1
                    ),
                },
                status_code=202,
            )

        except Exception as e:
            processing_time = (time.time() - start_time) * 1000
            logging.error(
                f"Webhook parsing failed in {processing_time:.1f}ms: {e}"
            )

            # Still return success to qBittorrent to avoid retries
            return JSONResponse(
                {"status": "error", "message": "Parsing failed"},
                status_code=202
            )

    async def start_event_processor(self):
        """Start background event processor"""
        if self._running:
            return

        self._running = True
        await self.cross_seed_forwarder.start()

        # Start background processing task
        self._processing_task = asyncio.create_task(
            self._process_events_loop()
        )
        logging.info("Webhook event processor started")

    async def stop(self):
        """Stop event processor"""
        self._running = False

        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        await self.cross_seed_forwarder.stop()
        logging.info("Webhook event processor stopped")

    async def _process_events_loop(self):
        """Main event processing loop - runs independently of webhook
        responses"""
        logging.info("Event processing loop started")

        while self._running:
            try:
                # Get event from queue (blocks until available or timeout)
                try:
                    event_data = await asyncio.wait_for(
                        self.event_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue  # Check if still running

                # Create event object
                event = WebhookEvent(event_data)

                # Process event safely (can take unlimited time)
                await self._process_event_safely(event)

                # Mark task done
                self.event_queue.task_done()

            except Exception as e:
                logging.error(f"Event processing loop error: {e}")
                # Continue processing other events

    async def _process_event_safely(self, event: WebhookEvent):
        """
        Process single event with error isolation

        This can take as long as needed - it runs in background
        """
        start_time = time.time()

        try:
            logging.debug(f"Processing event: {event}")

            # Handle different event types
            if event.event == "complete":
                await self._handle_completion_event(event)
            elif event.event == "add":
                await self._handle_add_event(event)
            elif event.event == "delete":
                await self._handle_delete_event(event)

            self.stats["events_processed"] = (self.stats["events_processed"] or 0) + 1
            processing_time = time.time() - start_time

            if processing_time > 1.0:  # Log slow processing
                logging.info(
                    f"Slow event processing: {event.event} took "
                    f"{processing_time:.2f}s"
                )

        except Exception as e:
            self.stats["processing_errors"] = (self.stats["processing_errors"] or 0) + 1
            logging.warning(f"Event processing failed for {event}: {e}")
            # Don't raise - this would stop the processing loop

    async def _handle_completion_event(self, event: WebhookEvent):
        """Handle torrent completion event"""
        # Forward to cross-seed (with retry)
        await self.cross_seed_forwarder.forward_with_retry(event)

        # Mark torrent for priority check in next allocation cycle
        if self.allocation_engine:
            await self.allocation_engine.mark_torrent_for_check(event.hash)

    async def _handle_add_event(self, event: WebhookEvent):
        """Handle torrent add event"""
        # New torrent added - ensure it gets processed in next cycle
        if self.allocation_engine:
            await self.allocation_engine.mark_torrent_for_check(event.hash)

            # If we know the tracker, we can trigger immediate tracker update
            if event.tracker:
                await self.allocation_engine.schedule_tracker_update(
                    event.tracker
                )

    async def _handle_delete_event(self, event: WebhookEvent):
        """Handle torrent delete event"""
        # Remove from cache and rollback data
        if self.allocation_engine:
            await self.allocation_engine.handle_torrent_deletion(event.hash)

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        return {
            "queue_size": self.event_queue.qsize(),
            "events_received": self.stats["events_received"],
            "events_processed": self.stats["events_processed"],
            "events_dropped": self.stats["events_dropped"],
            "processing_errors": self.stats["processing_errors"],
            "last_event_time": self.stats["last_event_time"],
            "processing_rate": self._calculate_processing_rate(),
        }

    def _calculate_processing_rate(self) -> float:
        """Calculate events processed per second"""
        if self.stats["events_processed"] == 0:
            return 0.0

        uptime = time.time() - (self.stats["last_event_time"] or time.time())
        if uptime <= 0:
            return 0.0

        return self.stats["events_processed"] / uptime

    def get_cross_seed_stats(self) -> Dict[str, Any]:
        """Get cross-seed forwarding statistics"""
        return self.cross_seed_forwarder.get_stats()

    async def drain_queue(self, timeout: float = 5.0):
        """Wait for queue to be processed (for testing)"""
        try:
            await asyncio.wait_for(self.event_queue.join(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logging.warning(f"Queue drain timeout after {timeout}s")
            return False
