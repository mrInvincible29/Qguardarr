"""Tracker pattern matching for torrent trackers"""

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

from src.config import TrackerConfig


class TrackerMatcher:
    """Efficient tracker pattern matching with caching"""

    def __init__(self, tracker_configs: List[TrackerConfig]):
        self.tracker_configs = tracker_configs
        self.patterns: Dict[str, re.Pattern] = {}
        self.tracker_cache: Dict[str, str] = {}  # URL hash -> tracker_id
        self.stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "pattern_matches": 0,
            "failed_matches": 0,
        }

        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile all regex patterns for efficiency"""
        self.patterns = {}

        for config in self.tracker_configs:
            try:
                pattern_text = self._normalize_pattern(config.pattern)
                self.patterns[config.id] = re.compile(pattern_text, re.IGNORECASE)
                logging.debug(
                    f"Compiled pattern for tracker {config.id}: {pattern_text}"
                )
            except re.error as e:
                logging.error(f"Invalid regex pattern for tracker {config.id}: {e}")

        logging.info(f"Compiled {len(self.patterns)} tracker patterns")

    def _normalize_pattern(self, pattern: str) -> str:
        """Make simple domain patterns more forgiving.

        - If pattern is anchored (^ or $), leave as-is.
        - If pattern lacks leading wildcard, add ".*" (also convert single leading '.' to '.*').
        - If pattern lacks trailing wildcard, add ".*" (also convert single trailing '.' to '.*').
        This helps users who write patterns like ".example\\.com." instead of ".*example\\.com.*".
        """
        s = pattern.strip()
        if not s:
            return s
        # Respect explicit anchors
        if s.startswith("^") or s.endswith("$"):
            return s
        # Normalize leading
        if s.startswith(".*"):
            pass
        elif s.startswith("."):
            s = ".*" + s[1:]
        else:
            s = ".*" + s
        # Normalize trailing
        if s.endswith(".*"):
            pass
        elif s.endswith("."):
            s = s[:-1] + ".*"
        else:
            s = s + ".*"
        return s

    def _get_cache_key(self, tracker_url: str) -> str:
        """Generate cache key for tracker URL"""
        # Use domain + path for caching to handle different parameters
        try:
            parsed = urlparse(tracker_url.lower())
            cache_key = f"{parsed.netloc}{parsed.path}"
            return hashlib.md5(cache_key.encode()).hexdigest()[:16]
        except Exception:
            return hashlib.md5(tracker_url.lower().encode()).hexdigest()[:16]

    def match_tracker(self, tracker_url: str) -> str:
        """
        Match tracker URL to configured tracker ID

        Args:
            tracker_url: Full tracker URL

        Returns:
            tracker_id: Matched tracker ID or 'default' if no match
        """
        if not tracker_url:
            return self._get_default_tracker_id()

        # Check cache first
        cache_key = self._get_cache_key(tracker_url)
        if cache_key in self.tracker_cache:
            self.stats["cache_hits"] += 1
            return self.tracker_cache[cache_key]

        self.stats["cache_misses"] += 1

        # Try to match patterns in order
        matched_tracker_id = self._find_matching_tracker(tracker_url)

        # Cache the result
        self.tracker_cache[cache_key] = matched_tracker_id

        if matched_tracker_id != self._get_default_tracker_id():
            self.stats["pattern_matches"] += 1
        else:
            self.stats["failed_matches"] += 1

        return matched_tracker_id

    def _find_matching_tracker(self, tracker_url: str) -> str:
        """Find the first matching tracker pattern"""
        # Try each tracker pattern in order (except default)
        for config in self.tracker_configs:
            if config.pattern == ".*":  # Skip catch-all pattern
                continue

            pattern = self.patterns.get(config.id)
            if pattern and pattern.search(tracker_url):
                logging.debug(f"Tracker {tracker_url} matched pattern {config.id}")
                return config.id

        # No match found, use default
        return self._get_default_tracker_id()

    def _get_default_tracker_id(self) -> str:
        """Get default tracker ID (should be the catch-all pattern)"""
        for config in self.tracker_configs:
            if config.pattern == ".*":
                return config.id

        # Fallback if no default configured
        return "default"

    def get_tracker_config(self, tracker_id: str) -> Optional[TrackerConfig]:
        """Get tracker configuration by ID"""
        for config in self.tracker_configs:
            if config.id == tracker_id:
                return config
        return None

    def get_all_tracker_configs(self) -> List[TrackerConfig]:
        """Get all tracker configurations"""
        return self.tracker_configs.copy()

    def bulk_match_trackers(self, tracker_urls: List[str]) -> Dict[str, str]:
        """
        Efficiently match multiple tracker URLs

        Args:
            tracker_urls: List of tracker URLs

        Returns:
            Dict mapping URL to tracker_id
        """
        results = {}

        for url in tracker_urls:
            results[url] = self.match_tracker(url)

        return results

    def group_torrents_by_tracker(self, torrents: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Group torrents by their matched tracker

        Args:
            torrents: List of torrent dictionaries with 'hash' and 'tracker'
                keys

        Returns:
            Dict mapping tracker_id to list of torrents
        """
        grouped: Dict[str, List[Dict]] = {}

        for torrent in torrents:
            tracker_id = self.match_tracker(torrent.get("tracker", ""))

            if tracker_id not in grouped:
                grouped[tracker_id] = []
            grouped[tracker_id].append(torrent)

        return grouped

    def get_tracker_limits(self) -> Dict[str, int]:
        """Get max upload speeds for all trackers"""
        limits = {}
        for config in self.tracker_configs:
            limits[config.id] = config.max_upload_speed
        return limits

    def get_tracker_priorities(self) -> Dict[str, int]:
        """Get priorities for all trackers"""
        priorities = {}
        for config in self.tracker_configs:
            priorities[config.id] = config.priority
        return priorities

    def update_tracker_configs(self, new_configs: List[TrackerConfig]) -> None:
        """Update tracker configurations (for hot reload)"""
        self.tracker_configs = new_configs
        self.patterns.clear()
        # Clear cache to ensure new patterns are used
        self.tracker_cache.clear()
        self._compile_patterns()

        logging.info(f"Updated tracker configurations: {len(new_configs)} trackers")

    def clear_cache(self) -> None:
        """Clear the tracker matching cache"""
        cache_size = len(self.tracker_cache)
        self.tracker_cache.clear()
        logging.debug(f"Cleared tracker cache: {cache_size} entries")

    def get_cache_stats(self) -> Dict[str, Union[int, float]]:
        """Get cache performance statistics"""
        total_requests = self.stats["cache_hits"] + self.stats["cache_misses"]
        hit_rate = (
            (self.stats["cache_hits"] / total_requests * 100)
            if total_requests > 0
            else 0
        )

        return {
            "cache_size": len(self.tracker_cache),
            "cache_hits": self.stats["cache_hits"],
            "cache_misses": self.stats["cache_misses"],
            "hit_rate_percent": round(hit_rate, 1),
            "pattern_matches": self.stats["pattern_matches"],
            "failed_matches": self.stats["failed_matches"],
        }

    def validate_patterns(self) -> List[str]:
        """
        Validate all tracker patterns and return list of errors

        Returns:
            List of error messages (empty if all valid)
        """
        errors = []

        # Check for catch-all pattern
        has_catchall = any(config.pattern == ".*" for config in self.tracker_configs)
        if not has_catchall:
            errors.append("No catch-all pattern (.*) found - add a default tracker")

        # Check pattern compilation
        for config in self.tracker_configs:
            try:
                re.compile(config.pattern, re.IGNORECASE)
            except re.error as e:
                errors.append(f"Invalid pattern for tracker {config.id}: {e}")

        # Check for duplicate IDs
        ids = [config.id for config in self.tracker_configs]
        if len(ids) != len(set(ids)):
            errors.append("Duplicate tracker IDs found")

        return errors

    def test_pattern_match(
        self, tracker_url: str, detailed: bool = False
    ) -> Dict[str, Any]:
        """
        Test tracker URL against all patterns for debugging

        Args:
            tracker_url: URL to test
            detailed: Whether to return detailed match info

        Returns:
            Match information
        """
        result: Dict[str, Any] = {
            "url": tracker_url,
            "matched_tracker": self.match_tracker(tracker_url),
            "matches": [],
        }

        if detailed:
            for config in self.tracker_configs:
                pattern = self.patterns.get(config.id)
                if pattern:
                    matches = pattern.search(tracker_url) is not None
                    result["matches"].append(
                        {
                            "tracker_id": config.id,
                            "pattern": config.pattern,
                            "matches": matches,
                        }
                    )

        return result
