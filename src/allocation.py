"""Basic allocation engine for Phase 1 - hard limits with equal distribution"""

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from src.config import QguardarrConfig
from src.qbit_client import QBittorrentClient, TorrentInfo
from src.rollback import RollbackManager
from src.tracker_matcher import TrackerMatcher


class TorrentCache:
    """Efficient storage for actively managed torrents"""

    def __init__(self, capacity: int = 5000):
        self.capacity = capacity
        self.hash_to_index: Dict[str, int] = {}
        self.free_slots: List[int] = list(range(capacity))
        self.used_count = 0

        # Compact arrays for frequently accessed data
        self.hashes: List[str] = [""] * capacity
        self.tracker_ids: List[str] = [""] * capacity
        self.upload_speeds = np.zeros(capacity, dtype=np.float32)
        self.current_limits = np.zeros(capacity, dtype=np.int32)
        self.last_seen = np.zeros(capacity, dtype=np.uint32)
        self.needs_update = np.zeros(capacity, dtype=bool)

    def add_torrent(
        self,
        torrent_hash: str,
        tracker_id: str,
        upload_speed: float,
        current_limit: int,
    ) -> bool:
        """Add torrent to cache"""
        if not self.free_slots:
            return False  # Cache full

        index = self.free_slots.pop()
        self.hash_to_index[torrent_hash] = index
        self.hashes[index] = torrent_hash
        self.tracker_ids[index] = tracker_id
        self.upload_speeds[index] = upload_speed
        self.current_limits[index] = current_limit
        self.last_seen[index] = int(time.time())
        self.needs_update[index] = False
        self.used_count += 1
        return True

    def update_torrent(
        self, torrent_hash: str, upload_speed: float, current_limit: int
    ):
        """Update existing torrent data"""
        index = self.hash_to_index.get(torrent_hash)
        if index is not None:
            self.upload_speeds[index] = upload_speed
            self.current_limits[index] = current_limit
            self.last_seen[index] = int(time.time())

    def remove_torrent(self, torrent_hash: str) -> bool:
        """Remove torrent from cache"""
        index = self.hash_to_index.get(torrent_hash)
        if index is None:
            return False

        del self.hash_to_index[torrent_hash]
        self.free_slots.append(index)
        self.hashes[index] = ""
        self.tracker_ids[index] = ""
        self.upload_speeds[index] = 0.0
        self.current_limits[index] = 0
        self.last_seen[index] = 0
        self.needs_update[index] = False
        self.used_count -= 1
        return True

    def get_tracker_id(self, torrent_hash: str) -> Optional[str]:
        """O(1) tracker lookup"""
        index = self.hash_to_index.get(torrent_hash)
        return self.tracker_ids[index] if index is not None else None

    def get_current_limit(self, torrent_hash: str) -> Optional[int]:
        """Get current limit for torrent"""
        index = self.hash_to_index.get(torrent_hash)
        return int(self.current_limits[index]) if index is not None else None

    def mark_for_update(self, torrent_hash: str):
        """Mark torrent as needing limit update"""
        index = self.hash_to_index.get(torrent_hash)
        if index is not None:
            self.needs_update[index] = True

    def get_torrents_by_tracker(self, tracker_id: str) -> List[Tuple[str, float, int]]:
        """Get all torrents for a tracker:
        (hash, upload_speed, current_limit)"""
        torrents = []
        for hash_, index in self.hash_to_index.items():
            if self.tracker_ids[index] == tracker_id:
                torrents.append(
                    (
                        hash_,
                        float(self.upload_speeds[index]),
                        int(self.current_limits[index]),
                    )
                )
        return torrents

    def get_torrents_needing_update(self) -> List[Tuple[str, int]]:
        """Get torrents marked for update: (hash, current_limit)"""
        updates = []
        for hash_, index in self.hash_to_index.items():
            if self.needs_update[index]:
                updates.append((hash_, int(self.current_limits[index])))
                self.needs_update[index] = False  # Clear flag
        return updates

    def cleanup_old_torrents(self, max_age_seconds: int = 1800) -> int:
        """Remove torrents not seen recently"""
        current_time = int(time.time())
        cutoff = current_time - max_age_seconds

        to_remove = []
        for hash_, index in self.hash_to_index.items():
            if self.last_seen[index] < cutoff:
                to_remove.append(hash_)

        for hash_ in to_remove:
            self.remove_torrent(hash_)

        return len(to_remove)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            "used_count": self.used_count,
            "free_slots": len(self.free_slots),
            "capacity": self.capacity,
            "utilization_percent": round(self.used_count / self.capacity * 100, 1),
        }


class GradualRollout:
    """Safely test on subset of torrents first"""

    def __init__(self, rollout_percentage: int = 10):
        self.rollout_percentage = rollout_percentage

    def should_manage_torrent(self, torrent_hash: str) -> bool:
        """Deterministic selection based on hash"""
        if self.rollout_percentage >= 100:
            return True

        # Use hash for consistent selection
        hash_value = int(hashlib.md5(torrent_hash.encode()).hexdigest()[:8], 16)
        return (hash_value % 100) < self.rollout_percentage

    def update_rollout_percentage(self, percentage: int):
        """Update rollout percentage"""
        self.rollout_percentage = max(1, min(100, percentage))


class AllocationEngine:
    """Basic allocation engine for Phase 1 - hard limits only"""

    def __init__(
        self,
        config: QguardarrConfig,
        qbit_client: QBittorrentClient,
        tracker_matcher: TrackerMatcher,
        rollback_manager: RollbackManager,
    ):
        self.config = config
        self.qbit_client = qbit_client
        self.tracker_matcher = tracker_matcher
        self.rollback_manager = rollback_manager

        self.cache = TorrentCache(capacity=5000)
        self.gradual_rollout = GradualRollout(config.global_settings.rollout_percentage)

        # Priority queues for processing
        self.pending_checks: Set[str] = set()  # Torrent hashes to check
        self.pending_tracker_updates: Set[str] = set()  # Tracker IDs to update

        # Statistics
        self.stats = {
            "allocation_cycles": 0,
            "api_calls_last_cycle": 0,
            "torrents_processed": 0,
            "limits_applied": 0,
            "errors": 0,
            "last_cycle_duration": 0.0,
            "last_cycle_time": None,
            "active_torrents": 0,
            "managed_torrents": 0,
        }

    async def run_allocation_cycle(self):
        """Main allocation cycle - basic implementation for Phase 1"""
        start_time = time.time()
        self.stats["allocation_cycles"] += 1
        self.stats["api_calls_last_cycle"] = 0

        try:
            logging.debug("Starting allocation cycle")

            # Step 1: Get active torrents from qBittorrent
            active_torrents = await self._get_active_torrents()
            self.stats["active_torrents"] = len(active_torrents)

            # Step 2: Filter torrents for gradual rollout
            managed_torrents = self._filter_torrents_for_rollout(active_torrents)
            self.stats["managed_torrents"] = len(managed_torrents)

            # Step 3: Update cache with current torrent data
            await self._update_cache(managed_torrents)

            # Step 4: Calculate new limits (hard limits for Phase 1)
            new_limits = self._calculate_limits_phase1(managed_torrents)

            # Step 5: Apply only necessary changes (differential updates)
            changes_applied = await self._apply_differential_updates(new_limits)
            self.stats["limits_applied"] += changes_applied

            # Step 6: Cleanup old cache entries
            cleaned = self.cache.cleanup_old_torrents()
            if cleaned > 0:
                logging.debug(f"Cleaned up {cleaned} old cache entries")

            duration = time.time() - start_time
            self.stats["last_cycle_duration"] = duration
            self.stats["last_cycle_time"] = time.time()

            logging.info(
                f"Allocation cycle completed: {len(managed_torrents)} "
                f"managed torrents, {changes_applied} limits updated, "
                f"{duration:.2f}s"
            )

        except Exception as e:
            self.stats["errors"] += 1
            logging.error(f"Allocation cycle failed: {e}")
            raise

    async def _get_active_torrents(self) -> List[TorrentInfo]:
        """Get active torrents from qBittorrent"""
        try:
            # Only get uploading torrents to reduce API calls
            torrents = await self.qbit_client.get_torrents(filter_active=True)
            self.stats["api_calls_last_cycle"] += 1

            # Also include recently active torrents from cache
            cached_hashes = set(self.cache.hash_to_index.keys())
            if cached_hashes:
                all_torrents = await self.qbit_client.get_torrents(filter_active=False)
                self.stats["api_calls_last_cycle"] += 1

                # Add cached torrents that might not be actively uploading now
                active_hashes = {t.hash for t in torrents}
                for torrent in all_torrents:
                    if (
                        torrent.hash in cached_hashes
                        and torrent.hash not in active_hashes
                    ):
                        torrents.append(torrent)

            return torrents

        except Exception as e:
            logging.error(f"Failed to get active torrents: {e}")
            return []

    def _filter_torrents_for_rollout(
        self, torrents: List[TorrentInfo]
    ) -> List[TorrentInfo]:
        """Filter torrents based on gradual rollout percentage"""
        if self.gradual_rollout.rollout_percentage >= 100:
            return torrents

        filtered = []
        for torrent in torrents:
            if self.gradual_rollout.should_manage_torrent(torrent.hash):
                filtered.append(torrent)

        total = len(torrents)
        managed = len(filtered)
        if total > 0:
            logging.debug(
                f"Rollout filter: managing {managed}/{total} torrents "
                f"({managed / total * 100:.1f}%)"
            )

        return filtered

    async def _update_cache(self, torrents: List[TorrentInfo]):
        """Update cache with current torrent data"""
        for torrent in torrents:
            # Match tracker
            tracker_id = self.tracker_matcher.match_tracker(torrent.tracker)

            # Get current limit from qBittorrent if not in cache
            current_limit = self.cache.get_current_limit(torrent.hash)
            if current_limit is None:
                current_limit = await self.qbit_client.get_torrent_upload_limit(
                    torrent.hash
                )
                self.stats["api_calls_last_cycle"] += 1

            # Update or add to cache
            if torrent.hash in self.cache.hash_to_index:
                self.cache.update_torrent(torrent.hash, torrent.upspeed, current_limit)
            else:
                self.cache.add_torrent(
                    torrent.hash, tracker_id, torrent.upspeed, current_limit
                )

    def _calculate_limits_phase1(self, torrents: List[TorrentInfo]) -> Dict[str, int]:
        """
        Calculate new limits using Phase 1 logic: hard limits with equal
        distribution
        """
        new_limits = {}

        # Group torrents by tracker
        tracker_groups = {}
        for torrent in torrents:
            tracker_id = self.tracker_matcher.match_tracker(torrent.tracker)
            if tracker_id not in tracker_groups:
                tracker_groups[tracker_id] = []
            tracker_groups[tracker_id].append(torrent)

        # Calculate limits for each tracker
        for tracker_id, tracker_torrents in tracker_groups.items():
            tracker_config = self.tracker_matcher.get_tracker_config(tracker_id)
            if not tracker_config:
                continue

            tracker_limit = tracker_config.max_upload_speed

            # If tracker is configured as unlimited (-1), remove caps
            if tracker_limit <= 0:
                for torrent in tracker_torrents:
                    new_limits[torrent.hash] = -1  # unlimited
                continue

            # Simple equal distribution for Phase 1
            if len(tracker_torrents) == 1:
                # Single torrent gets full tracker limit
                new_limits[tracker_torrents[0].hash] = tracker_limit
            else:
                # Multiple torrents share equally
                per_torrent_limit = tracker_limit // len(tracker_torrents)
                min_limit = 10240  # Minimum 10 KB/s per torrent

                if per_torrent_limit < min_limit:
                    per_torrent_limit = min_limit

                for torrent in tracker_torrents:
                    new_limits[torrent.hash] = per_torrent_limit

        return new_limits

    async def _apply_differential_updates(self, new_limits: Dict[str, int]) -> int:
        """Apply only limits that need updating"""
        updates_needed = {}

        for torrent_hash, new_limit in new_limits.items():
            current_limit = self.cache.get_current_limit(torrent_hash)
            if current_limit is None:
                # New torrent, definitely needs update
                updates_needed[torrent_hash] = new_limit
            elif self.qbit_client.needs_update(
                current_limit,
                new_limit,
                self.config.global_settings.differential_threshold,
            ):
                updates_needed[torrent_hash] = new_limit

        if not updates_needed:
            logging.debug("No limit updates needed")
            return 0

        logging.debug(f"Updating limits for {len(updates_needed)} torrents")

        # Record changes for rollback before applying
        rollback_entries = []
        for torrent_hash, new_limit in updates_needed.items():
            current_limit = self.cache.get_current_limit(torrent_hash) or -1
            tracker_id = self.cache.get_tracker_id(torrent_hash) or "unknown"
            rollback_entries.append(
                (
                    torrent_hash,
                    current_limit,
                    new_limit,
                    tracker_id,
                    "allocation_update",
                )
            )

        await self.rollback_manager.record_batch_changes(rollback_entries)

        # Apply updates in batches
        await self.qbit_client.set_torrents_upload_limits_batch(updates_needed)
        self.stats["api_calls_last_cycle"] += (
            len(updates_needed) // 50 + 1
        )  # Estimate batch API calls

        # Update cache with new limits
        for torrent_hash, new_limit in updates_needed.items():
            index = self.cache.hash_to_index.get(torrent_hash)
            if index is not None:
                self.cache.current_limits[index] = new_limit

        return len(updates_needed)

    async def mark_torrent_for_check(self, torrent_hash: str):
        """Mark torrent for priority checking in next cycle"""
        self.pending_checks.add(torrent_hash)

    async def schedule_tracker_update(self, tracker_url: str):
        """Schedule immediate update for specific tracker"""
        tracker_id = self.tracker_matcher.match_tracker(tracker_url)
        self.pending_tracker_updates.add(tracker_id)

    async def handle_torrent_deletion(self, torrent_hash: str):
        """Handle torrent deletion event"""
        self.cache.remove_torrent(torrent_hash)
        self.pending_checks.discard(torrent_hash)

    def update_rollout_percentage(self, percentage: int):
        """Update gradual rollout percentage"""
        self.gradual_rollout.update_rollout_percentage(percentage)
        self.config.global_settings.rollout_percentage = percentage

    def get_stats(self) -> Dict[str, Any]:
        """Get basic statistics"""
        stats = self.stats.copy()
        stats.update(self.cache.get_stats())
        stats["rollout_percentage"] = self.gradual_rollout.rollout_percentage
        return stats

    def get_detailed_stats(self) -> Dict[str, Any]:
        """Get detailed statistics"""
        stats = self.get_stats()

        # Add rollback stats
        # Note: In real implementation, we'd await rollback stats properly

        # Add tracker matcher stats
        stats["cache_stats"] = self.tracker_matcher.get_cache_stats()

        # Add memory usage estimate
        # Rough estimate
        cache_size_mb = (self.cache.used_count * 200) / (1024 * 1024)
        stats["estimated_memory_mb"] = round(cache_size_mb, 2)

        return stats

    def get_tracker_stats(self) -> Dict[str, Any]:
        """Get per-tracker statistics"""
        tracker_stats = {}

        # Get configured limits
        for tracker_config in self.tracker_matcher.get_all_tracker_configs():
            tracker_stats[tracker_config.id] = {
                "name": tracker_config.name,
                "configured_limit_mbps": round(
                    tracker_config.max_upload_speed / (1024 * 1024), 2
                ),
                "priority": tracker_config.priority,
                "active_torrents": 0,
                "current_usage_mbps": 0.0,
            }

        # Add current usage data
        for tracker_id in tracker_stats.keys():
            torrents = self.cache.get_torrents_by_tracker(tracker_id)
            tracker_stats[tracker_id]["active_torrents"] = len(torrents)

            total_usage = sum(upload_speed for _, upload_speed, _ in torrents)
            tracker_stats[tracker_id]["current_usage_mbps"] = round(
                total_usage / (1024 * 1024), 2
            )

        return tracker_stats
