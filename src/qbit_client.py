"""qBittorrent API client wrapper"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel

from src.config import QBittorrentSettings


class TorrentInfo(BaseModel):
    """Torrent information from qBittorrent"""

    hash: str
    name: str
    state: str
    progress: float
    dlspeed: int
    upspeed: int
    priority: int
    num_seeds: int
    num_leechs: int
    ratio: float
    size: int
    completed: int
    tracker: str = ""
    category: str = ""
    tags: str = ""
    added_on: int = 0
    completion_on: int = 0
    last_activity: int = 0

    @property
    def upload_speed_kb(self) -> float:
        """Upload speed in KB/s"""
        return self.upspeed / 1024.0

    @property
    def is_active(self) -> bool:
        """Check if torrent is actively uploading"""
        return self.upspeed > 0

    @property
    def num_peers(self) -> int:
        """Total number of peers"""
        return self.num_seeds + self.num_leechs


class APICircuitBreaker:
    """Circuit breaker to protect against API overload"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half-open

    def can_execute(self) -> bool:
        """Check if API call can be executed"""
        if self.state == "closed":
            return True

        if self.state == "open":
            if (
                self.last_failure_time
                and time.time() - self.last_failure_time > self.recovery_timeout
            ):
                self.state = "half-open"
                return True
            return False

        # half-open state
        return True

    def on_success(self):
        """Record successful API call"""
        if self.state == "half-open":
            self.state = "closed"
            self.failure_count = 0

    def on_failure(self):
        """Record failed API call"""
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            self.last_failure_time = time.time()


class QBittorrentClient:
    """qBittorrent Web API client with circuit breaker and rate limiting"""

    def __init__(self, config: QBittorrentSettings):
        self.config = config
        self.base_url = f"http://{config.host}:{config.port}"
        self.session: Optional[httpx.AsyncClient] = None
        self.authenticated = False
        self.circuit_breaker = APICircuitBreaker()
        self.last_request_time = 0.0
        self.min_request_interval = 0.1  # 100ms between requests

        # Statistics
        self.stats = {
            "api_calls": 0,
            "api_failures": 0,
            "last_error": None,
            "connected": False,
            "auth_time": None,
        }

    async def connect(self):
        """Initialize connection and authenticate"""
        if self.session:
            await self.session.aclose()

        self.session = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

        await self._authenticate()
        self.stats["connected"] = True
        self.stats["auth_time"] = time.time()
        logging.info("Connected to qBittorrent successfully")

    async def disconnect(self):
        """Close connection"""
        if self.session:
            try:
                await self.session.post(f"{self.base_url}/api/v2/auth/logout")
            except Exception:
                pass  # Ignore logout errors

            await self.session.aclose()
            self.session = None

        self.authenticated = False
        self.stats["connected"] = False
        logging.info("Disconnected from qBittorrent")

    async def _authenticate(self):
        """Authenticate using only configured credentials; never log raw secrets."""
        if not self.session:
            raise RuntimeError("Session not initialized")

        def mask(s: str) -> str:
            if not s:
                return "(empty)"
            if len(s) <= 4:
                return "****"
            return s[0] + "***" + s[-1]

        try:
            logging.info(
                "Authenticating to qBittorrent with configured credentials (user=%s, pass=%s)",
                self.config.username,
                "******",
            )
            login_data = {
                "username": self.config.username,
                "password": self.config.password,
            }
            response = await self.session.post(
                f"{self.base_url}/api/v2/auth/login", data=login_data
            )
            response.raise_for_status()
            if response.text.strip() == "Ok.":
                self.authenticated = True
                logging.debug("Authenticated with qBittorrent")
                return
            raise RuntimeError(f"Authentication failed: {response.text.strip()}")
        except Exception as e:
            raise RuntimeError(f"Authentication failed: {e}")

    async def _make_request(
        self, method: str, endpoint: str, **kwargs
    ) -> httpx.Response:
        """Make authenticated API request with circuit breaker"""
        if not self.circuit_breaker.can_execute():
            raise RuntimeError("Circuit breaker is open")

        if not self.session or not self.authenticated:
            await self.connect()

        # Rate limiting
        now = time.time()
        time_since_last = now - self.last_request_time
        if time_since_last < self.min_request_interval:
            await asyncio.sleep(self.min_request_interval - time_since_last)

        self.last_request_time = time.time()

        try:
            url = f"{self.base_url}{endpoint}"
            response = await self.session.request(method, url, **kwargs)

            # Check for authentication errors
            if response.status_code == 403:
                logging.warning("Authentication expired, re-authenticating...")
                await self._authenticate()
                response = await self.session.request(method, url, **kwargs)

            response.raise_for_status()
            self.circuit_breaker.on_success()
            self.stats["api_calls"] += 1
            return response

        except Exception as e:
            self.circuit_breaker.on_failure()
            self.stats["api_failures"] += 1
            self.stats["last_error"] = str(e)
            logging.error(f"API request failed: {e}")
            raise

    async def get_torrents(
        self, filter_active: bool = True, min_upload_bps: int = 1
    ) -> List[TorrentInfo]:
        """Get list of torrents.

        When filter_active is True, requests qBittorrent with filter=active and
        then keeps only torrents with upspeed >= min_upload_bps. Trackers are
        fetched only for the filtered subset to reduce API calls at scale.
        """
        params = {}
        if filter_active:
            params["filter"] = "active"

        response = await self._make_request(
            "GET", "/api/v2/torrents/info", params=params
        )
        torrents_data = response.json()

        # Pre-filter by actual upload speed if active filter requested
        if filter_active:
            torrents_data = [
                t
                for t in torrents_data
                if int(t.get("upspeed", 0)) >= int(min_upload_bps)
            ]

        torrents: List[TorrentInfo] = []
        for torrent_data in torrents_data:
            # Get primary tracker only for the subset we plan to manage
            tracker_info = await self._get_torrent_tracker(torrent_data["hash"])
            torrent_data["tracker"] = tracker_info

            torrent = TorrentInfo(**torrent_data)
            torrents.append(torrent)

        return torrents

    async def _get_torrent_tracker(self, torrent_hash: str) -> str:
        """Get primary tracker for torrent"""
        try:
            response = await self._make_request(
                "GET", "/api/v2/torrents/trackers", params={"hash": torrent_hash}
            )
            trackers = response.json()

            # Find working tracker (status 2 = working)
            for tracker in trackers:
                if tracker.get("status") == 2 and tracker.get("url"):
                    return tracker["url"]

            # Fallback to first tracker with URL
            for tracker in trackers:
                if tracker.get("url") and not tracker["url"].startswith("**"):
                    return tracker["url"]

            return ""

        except Exception as e:
            logging.debug(f"Failed to get tracker for {torrent_hash}: {e}")
            return ""

    async def set_torrent_upload_limit(self, torrent_hash: str, limit: int):
        """Set upload limit for single torrent"""
        data = {"hashes": torrent_hash, "limit": limit}

        await self._make_request("POST", "/api/v2/torrents/setUploadLimit", data=data)

    async def set_torrents_upload_limits_batch(
        self, limits: Dict[str, int], batch_size: int = 50
    ):
        """Set upload limits for multiple torrents in batches"""
        if not limits:
            return

        # Group by limit value for efficiency
        limits_groups: Dict[int, List[str]] = {}
        for torrent_hash, limit in limits.items():
            if limit not in limits_groups:
                limits_groups[limit] = []
            limits_groups[limit].append(torrent_hash)

        # Process each limit group
        for limit, torrent_hashes in limits_groups.items():
            # Process in batches
            for i in range(0, len(torrent_hashes), batch_size):
                batch = torrent_hashes[i : i + batch_size]
                hashes_str = "|".join(batch)

                data = {"hashes": hashes_str, "limit": limit}

                await self._make_request(
                    "POST", "/api/v2/torrents/setUploadLimit", data=data
                )

                # Small delay between batches
                await asyncio.sleep(0.1)

    async def get_torrent_upload_limit(self, torrent_hash: str) -> int:
        """Get current upload limit for torrent"""
        response = await self._make_request(
            "GET", "/api/v2/torrents/properties", params={"hash": torrent_hash}
        )

        properties = response.json()
        return properties.get("up_limit", -1)  # -1 means unlimited

    async def remove_torrent_upload_limits(
        self, torrent_hashes: List[str], batch_size: int = 50
    ):
        """Remove upload limits (set to unlimited)"""
        limits = {hash_: -1 for hash_ in torrent_hashes}
        await self.set_torrents_upload_limits_batch(limits, batch_size)

    async def get_global_stats(self) -> Dict[str, Any]:
        """Get global transfer stats"""
        response = await self._make_request("GET", "/api/v2/transfer/info")
        return response.json()

    async def get_preferences(self) -> Dict[str, Any]:
        """Get qBittorrent preferences"""
        response = await self._make_request("GET", "/api/v2/app/preferences")
        return response.json()

    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics"""
        return self.stats.copy()

    def needs_update(
        self, current_limit: int, new_limit: int, threshold: float = 0.2
    ) -> bool:
        """
        Check if torrent limit needs updating based on differential threshold

        Args:
            current_limit: Current upload limit (-1 for unlimited)
            new_limit: New proposed limit
            threshold: Minimum relative change to trigger update
        """
        # Always update when crossing zero/unlimited boundary
        current_is_unlimited = current_limit <= 0
        new_is_unlimited = new_limit <= 0

        if current_is_unlimited != new_is_unlimited:
            return True

        # If both unlimited, no update needed
        if current_is_unlimited and new_is_unlimited:
            return False

        # For very small speeds, use absolute threshold to avoid noise
        if max(current_limit, new_limit) < 51200:  # <50KB/s
            return abs(current_limit - new_limit) > 10240  # 10KB/s threshold

        # For medium speeds, use combined approach
        if max(current_limit, new_limit) < 1048576:  # <1MB/s
            abs_change = abs(current_limit - new_limit)
            rel_change = abs_change / max(current_limit, 1)
            return abs_change > 51200 or rel_change > 0.3  # 50KB/s OR 30%

        # For high speeds, use percentage with minimum absolute requirement
        abs_change = abs(current_limit - new_limit)
        rel_change = abs_change / max(current_limit, 1)
        return abs_change > 102400 and rel_change > threshold  # 100KB/s AND 15%

    async def add_torrent_from_magnet(
        self, magnet_url: str, category: Optional[str] = None, paused: bool = False
    ) -> bool:
        """Add torrent from magnet link"""
        data = {"urls": magnet_url}

        if category:
            data["category"] = category
        if paused:
            data["paused"] = "true"

        try:
            await self._make_request("POST", "/api/v2/torrents/add", data=data)
            return True
        except Exception as e:
            logging.error(f"Failed to add torrent from magnet: {e}")
            return False

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False):
        """Delete torrent"""
        data = {
            "hashes": torrent_hash,
            "deleteFiles": "true" if delete_files else "false",
        }

        await self._make_request("POST", "/api/v2/torrents/delete", data=data)

    async def get_version(self) -> Dict[str, Any]:
        """Get qBittorrent version info"""
        response = await self._make_request("GET", "/api/v2/app/version")
        version_str = response.text.strip('"')  # Remove quotes

        # Also get build info
        try:
            build_response = await self._make_request("GET", "/api/v2/app/buildInfo")
            build_info = build_response.json()
            return {"version": version_str, "build_info": build_info}
        except Exception:
            return {"version": version_str}

    async def get_torrent_trackers(self, torrent_hash: str) -> List[Dict[str, Any]]:
        """Get trackers for a specific torrent"""
        response = await self._make_request(
            "GET", "/api/v2/torrents/trackers", params={"hash": torrent_hash}
        )

        trackers_data = response.json()

        # Convert to simplified format
        trackers = []
        for tracker_data in trackers_data:
            trackers.append(
                {
                    "url": tracker_data.get("url", ""),
                    "status": tracker_data.get("status", 0),
                    "tier": tracker_data.get("tier", 0),
                    "num_peers": tracker_data.get("num_peers", 0),
                    "num_seeds": tracker_data.get("num_seeds", 0),
                    "num_leeches": tracker_data.get("num_leeches", 0),
                    "msg": tracker_data.get("msg", ""),
                }
            )

        return trackers
