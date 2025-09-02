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


class ActivityScorer:
    """
    Score torrents based on how actively they need management.
    Conservative defaults match the Phase 2 plan.
    """

    def __init__(
        self, management_threshold: float = 0.5, max_managed_torrents: int = 1000
    ):
        self.management_threshold = management_threshold
        self.max_managed_torrents = max_managed_torrents

    def calculate_priority_score(self, torrent) -> float:
        """Return a 0-1 score indicating management priority.

        Heuristics:
        - If currently uploading >10KB/s → 1.0
        - Recent activity buckets: <1h → 0.8, <6h → 0.5, <24h → 0.2, else 0.0
        - Peer boost: +0.3 if peers >20, +0.1 if >5 (clamped to 1.0)
        """
        try:
            if getattr(torrent, "upspeed", 0) > 10 * 1024:  # >10KB/s
                return 1.0

            last_activity = getattr(torrent, "last_activity", 0) or 0
            now = int(time.time())
            hours_since_activity = max(0.0, (now - int(last_activity)) / 3600.0)

            if hours_since_activity < 1:
                score = 0.8
            elif hours_since_activity < 6:
                score = 0.5
            elif hours_since_activity < 24:
                score = 0.2
            else:
                score = 0.0

            # Peer boost based on potential
            num_peers = 0
            # Prefer attribute if provided; otherwise derive from seeds/leechers
            if hasattr(torrent, "num_peers"):
                try:
                    num_peers = int(torrent.num_peers)  # type: ignore[attr-defined]
                except Exception:
                    num_peers = 0
            else:
                num_peers = int(getattr(torrent, "num_seeds", 0)) + int(
                    getattr(torrent, "num_leechs", 0)
                )

            if num_peers > 20:
                score = min(1.0, score + 0.3)
            elif num_peers > 5:
                score = min(1.0, score + 0.1)

            return float(score)
        except Exception:
            # Defensive: never break allocation cycle due to scoring
            return 0.0

    def should_manage(self, torrent, available_slots: int, total_torrents: int) -> bool:
        """Decide whether to include a torrent in active management set."""
        score = self.calculate_priority_score(torrent)
        if score >= 0.8:
            return True
        if score >= 0.5:
            # Medium-priority torrents are generally worth managing when slots remain
            return available_slots > 0
        return score > 0.3 and available_slots > 500


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
        # Phase 2: scoring helper (used when weighted strategies are enabled)
        self.activity_scorer = ActivityScorer()
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
            # Phase 2 stats
            "managed_torrent_count": 0,
            "score_distribution": {"high": 0, "medium": 0, "low": 0, "ignored": 0},
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

            # Step 4: Calculate new limits (strategy-based)
            strategy = getattr(
                self.config.global_settings, "allocation_strategy", "equal"
            )
            torrents_for_calc = managed_torrents
            if strategy == "weighted":
                torrents_for_calc = self.select_torrents_for_management(
                    managed_torrents
                )
            if strategy == "weighted":
                new_limits = self._calculate_limits_phase2(torrents_for_calc)
            else:
                new_limits = self._calculate_limits_phase1(torrents_for_calc)

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

    def _calculate_limits_phase2(self, torrents: List[TorrentInfo]) -> Dict[str, int]:
        """
        Phase 2: Weighted distribution within each tracker based on simple scoring.

        Scoring per torrent within a tracker:
          score = 0.6 * min(peers/20, 1.0) + 0.4 * min(upload_speed/1MBps, 1.0)

        Then distribute tracker cap proportionally with per-torrent bounds:
          - min 10KB/s
          - max 60% of tracker cap
        """
        new_limits: Dict[str, int] = {}

        # Group torrents by tracker
        tracker_groups: Dict[str, List[TorrentInfo]] = {}
        for torrent in torrents:
            tracker_id = self.tracker_matcher.match_tracker(torrent.tracker)
            tracker_groups.setdefault(tracker_id, []).append(torrent)

        for tracker_id, group in tracker_groups.items():
            tracker_config = self.tracker_matcher.get_tracker_config(tracker_id)
            if not tracker_config:
                continue

            tracker_limit = tracker_config.max_upload_speed

            # Unlimited tracker: set all to unlimited
            if tracker_limit <= 0:
                for torrent in group:
                    new_limits[torrent.hash] = -1
                continue

            if len(group) == 1:
                new_limits[group[0].hash] = tracker_limit
                continue

            # Compute scores
            scores: Dict[str, float] = {}
            for t in group:
                peer_score = min(max(t.num_peers, 0) / 20.0, 1.0)
                speed_score = min(
                    max(t.upspeed, 0) / 1048576.0, 1.0
                )  # bytes per sec normalized by 1MB/s
                scores[t.hash] = 0.6 * peer_score + 0.4 * speed_score

            total_score = sum(scores.values())
            min_limit = 10240  # 10KB/s
            max_fraction = 0.6

            # First-pass proportional allocation
            allocations: Dict[str, float] = {}
            if total_score <= 0:
                # Equal split fallback
                for t in group:
                    allocations[t.hash] = tracker_limit / float(len(group))
            else:
                for t in group:
                    proportion = scores[t.hash] / total_score
                    allocations[t.hash] = tracker_limit * proportion

            # Apply per-torrent bounds
            capped: Dict[str, float] = {}
            max_cap = tracker_limit * max_fraction
            for h, alloc in allocations.items():
                capped[h] = max(min_limit, min(alloc, max_cap))

            total_alloc = sum(capped.values())

            if total_alloc < tracker_limit:
                # Distribute remaining to torrents with headroom up to their max_cap
                remaining = tracker_limit - total_alloc
                headroom: Dict[str, float] = {
                    h: max(0.0, max_cap - capped[h]) for h in capped
                }
                total_headroom = sum(headroom.values())
                if total_headroom > 0 and remaining > 0:
                    for h in capped:
                        share = (
                            remaining * (headroom[h] / total_headroom)
                            if total_headroom > 0
                            else 0
                        )
                        capped[h] = min(max_cap, capped[h] + share)

            elif total_alloc > tracker_limit:
                # Reduce proportionally but not below min_limit
                reduce_by = total_alloc - tracker_limit
                reducible: Dict[str, float] = {
                    h: max(0.0, capped[h] - min_limit) for h in capped
                }
                total_reducible = sum(reducible.values())
                if total_reducible > 0 and reduce_by > 0:
                    for h in capped:
                        cut = (
                            reduce_by * (reducible[h] / total_reducible)
                            if total_reducible > 0
                            else 0
                        )
                        capped[h] = max(min_limit, capped[h] - cut)

            # Finalize ints with clamps to maintain bounds after rounding
            max_int_cap = int(max_cap)
            for h, alloc in capped.items():
                v = int(round(alloc))
                if v < min_limit:
                    v = min_limit
                if v > max_int_cap:
                    v = max_int_cap
                new_limits[h] = v

            # Final correction for rounding while respecting bounds
            current_sum = sum(new_limits[h] for h in capped.keys())
            delta = tracker_limit - current_sum
            if delta != 0:
                if delta > 0:
                    # Try to add delta to an entry with headroom
                    candidates = sorted(
                        capped.keys(),
                        key=lambda x: (max_cap - new_limits[x]),
                        reverse=True,
                    )
                    for h in candidates:
                        head = int(max(0, round(max_cap) - new_limits[h]))
                        if head <= 0:
                            continue
                        add = min(delta, head)
                        new_limits[h] += add
                        delta -= add
                        if delta == 0:
                            break
                else:
                    # Remove |delta| while respecting min_limit
                    need = -delta
                    candidates = sorted(
                        capped.keys(),
                        key=lambda x: (new_limits[x] - min_limit),
                        reverse=True,
                    )
                    for h in candidates:
                        room = int(max(0, new_limits[h] - min_limit))
                        if room <= 0:
                            continue
                        cut = min(need, room)
                        new_limits[h] -= cut
                        need -= cut
                        if need == 0:
                            break

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

    # ------------------------- Phase 2 helpers -------------------------
    def select_torrents_for_management(self, all_torrents: List[Any]) -> List[Any]:
        """
        Select subset of torrents worth managing based on activity scoring.
        Returns the selected torrent objects, ordered by descending score.
        """
        # Reset distribution
        sd = {"high": 0, "medium": 0, "low": 0, "ignored": 0}

        scored: List[Tuple[float, Any]] = []
        for t in all_torrents:
            score = self.activity_scorer.calculate_priority_score(t)
            if score >= 0.8:
                sd["high"] += 1
            elif score >= 0.5:
                sd["medium"] += 1
            elif score >= 0.2:
                sd["low"] += 1
            else:
                sd["ignored"] += 1

            if score > 0.2:
                scored.append((score, t))

        # Sort by score desc
        scored.sort(key=lambda x: x[0], reverse=True)

        selected: List[Any] = []
        max_n = self.activity_scorer.max_managed_torrents

        for score, torrent in scored:
            if len(selected) >= max_n:
                break
            remaining = max_n - len(selected)
            if self.activity_scorer.should_manage(
                torrent, remaining, len(all_torrents)
            ):
                selected.append(torrent)

        self.stats["managed_torrent_count"] = len(selected)
        self.stats["score_distribution"] = sd
        return selected

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
