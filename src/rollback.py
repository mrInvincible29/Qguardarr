"""Rollback system for tracking and reversing torrent limit changes"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import aiosqlite

from src.config import RollbackSettings


class RollbackEntry:
    """Single rollback entry"""

    def __init__(
        self,
        torrent_hash: str,
        old_limit: int,
        new_limit: int,
        tracker_id: str,
        timestamp: float,
        reason: str = "",
    ):
        self.torrent_hash = torrent_hash
        self.old_limit = old_limit  # -1 for unlimited
        self.new_limit = new_limit
        self.tracker_id = tracker_id
        self.timestamp = timestamp
        self.reason = reason
        self.restored = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "torrent_hash": self.torrent_hash,
            "old_limit": self.old_limit,
            "new_limit": self.new_limit,
            "tracker_id": self.tracker_id,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "restored": self.restored,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RollbackEntry":
        """Create from dictionary"""
        entry = cls(
            torrent_hash=data["torrent_hash"],
            old_limit=data["old_limit"],
            new_limit=data["new_limit"],
            tracker_id=data["tracker_id"],
            timestamp=data["timestamp"],
            reason=data.get("reason", ""),
        )
        entry.restored = data.get("restored", False)
        return entry

    def __str__(self) -> str:
        old_str = "unlimited" if self.old_limit == -1 else f"{self.old_limit} B/s"
        new_str = "unlimited" if self.new_limit == -1 else f"{self.new_limit} B/s"
        return f"RollbackEntry({self.torrent_hash[:8]}... {old_str} -> {new_str})"


class RollbackManager:
    """Manages rollback database and operations"""

    def __init__(self, config: RollbackSettings):
        self.config = config
        self.db_path = Path(config.database_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.stats: Dict[str, Union[int, float, str, None]] = {
            "changes_recorded": 0,
            "rollbacks_performed": 0,
            "last_rollback_time": None,
            "database_size_mb": 0.0,
        }

    async def initialize(self):
        """Initialize database schema"""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS rollback_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    torrent_hash TEXT NOT NULL,
                    old_limit INTEGER NOT NULL,
                    new_limit INTEGER NOT NULL,
                    tracker_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    reason TEXT DEFAULT '',
                    restored INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            # Create indexes for performance
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_torrent_hash ON rollback_entries(torrent_hash)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON rollback_entries(timestamp)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_restored ON rollback_entries(restored)"
            )

            await db.commit()

        # Update database size stat
        await self._update_db_size_stat()
        logging.info(f"Rollback database initialized: {self.db_path}")

    async def record_change(
        self,
        torrent_hash: str,
        old_limit: int,
        new_limit: int,
        tracker_id: str,
        reason: str = "allocation_update",
    ) -> bool:
        """
        Record a torrent limit change for potential rollback

        Args:
            torrent_hash: Torrent hash
            old_limit: Previous limit (-1 for unlimited)
            new_limit: New limit (-1 for unlimited)
            tracker_id: Tracker ID
            reason: Reason for change

        Returns:
            True if recorded successfully
        """
        if not self.config.track_all_changes:
            return True  # Skip recording if disabled

        # Don't record no-op changes
        if old_limit == new_limit:
            return True

        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                await db.execute(
                    """
                    INSERT INTO rollback_entries
                    (torrent_hash, old_limit, new_limit, tracker_id, timestamp, reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        torrent_hash,
                        old_limit,
                        new_limit,
                        tracker_id,
                        time.time(),
                        reason,
                    ),
                )

                await db.commit()

            self.stats["changes_recorded"] = (self.stats["changes_recorded"] or 0) + 1
            logging.debug(
                f"Recorded rollback entry: {torrent_hash[:8]}... {old_limit} -> {new_limit}"
            )
            return True

        except Exception as e:
            logging.error(f"Failed to record rollback entry: {e}")
            return False

    async def record_batch_changes(
        self, changes: List[Tuple[str, int, int, str, str]]
    ) -> int:
        """
        Record multiple changes in batch for efficiency

        Args:
            changes: List of (hash, old_limit, new_limit, tracker_id, reason) tuples

        Returns:
            Number of changes recorded
        """
        if not self.config.track_all_changes or not changes:
            return 0

        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                timestamp = time.time()

                # Filter out no-op changes
                valid_changes = [
                    (hash_, old, new, tracker, reason, timestamp)
                    for hash_, old, new, tracker, reason in changes
                    if old != new
                ]

                if not valid_changes:
                    return 0

                await db.executemany(
                    """
                    INSERT INTO rollback_entries
                    (torrent_hash, old_limit, new_limit, tracker_id, reason, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    valid_changes,
                )

                await db.commit()

                self.stats["changes_recorded"] = (
                    self.stats["changes_recorded"] or 0
                ) + len(valid_changes)
                logging.debug(
                    f"Recorded {len(valid_changes)} rollback entries in batch"
                )
                return len(valid_changes)

        except Exception as e:
            logging.error(f"Failed to record batch rollback entries: {e}")
            return 0

    async def get_rollback_entries_for_torrent(
        self, torrent_hash: str, include_restored: bool = False
    ) -> List[RollbackEntry]:
        """Get rollback entries for specific torrent"""
        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                query = """
                    SELECT torrent_hash, old_limit, new_limit, tracker_id,
                           timestamp, reason, restored
                    FROM rollback_entries
                    WHERE torrent_hash = ?
                """

                if not include_restored:
                    query += " AND restored = 0"

                query += " ORDER BY timestamp DESC"

                async with db.execute(query, (torrent_hash,)) as cursor:
                    entries = []
                    async for row in cursor:
                        entry = RollbackEntry(
                            torrent_hash=row[0],
                            old_limit=row[1],
                            new_limit=row[2],
                            tracker_id=row[3],
                            timestamp=row[4],
                            reason=row[5] or "",
                        )
                        entry.restored = bool(row[6])
                        entries.append(entry)

                    return entries

        except Exception as e:
            logging.error(f"Failed to get rollback entries for {torrent_hash}: {e}")
            return []

    async def get_all_unrestored_entries(self) -> List[RollbackEntry]:
        """Get all entries that haven't been restored"""
        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                async with db.execute(
                    """
                    SELECT torrent_hash, old_limit, new_limit, tracker_id, timestamp, reason
                    FROM rollback_entries
                    WHERE restored = 0
                    ORDER BY timestamp ASC
                """
                ) as cursor:
                    entries = []
                    async for row in cursor:
                        entry = RollbackEntry(
                            torrent_hash=row[0],
                            old_limit=row[1],
                            new_limit=row[2],
                            tracker_id=row[3],
                            timestamp=row[4],
                            reason=row[5] or "",
                        )
                        entries.append(entry)

                    return entries

        except Exception as e:
            logging.error(f"Failed to get unrestored entries: {e}")
            return []

    async def get_distinct_hashes(self, include_restored: bool = True) -> List[str]:
        """Get distinct torrent hashes recorded in rollback DB.

        Args:
            include_restored: When False, only return hashes with unrestored entries.

        Returns:
            List of unique torrent hashes.
        """
        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                if include_restored:
                    query = "SELECT DISTINCT torrent_hash FROM rollback_entries"
                    params = ()
                else:
                    query = (
                        "SELECT DISTINCT torrent_hash FROM rollback_entries WHERE restored = 0"
                    )
                    params = ()
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]
        except Exception as e:
            logging.error(f"Failed to get distinct hashes: {e}")
            return []

    async def mark_entries_restored(self, torrent_hashes: List[str]) -> int:
        """Mark entries as restored"""
        if not torrent_hashes:
            return 0

        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                placeholders = ",".join("?" * len(torrent_hashes))
                result = await db.execute(
                    f"""
                    UPDATE rollback_entries
                    SET restored = 1
                    WHERE torrent_hash IN ({placeholders}) AND restored = 0
                """,
                    torrent_hashes,
                )

                await db.commit()
                return result.rowcount

        except Exception as e:
            logging.error(f"Failed to mark entries restored: {e}")
            return 0

    async def rollback_all_changes(self, reason: str = "manual_rollback") -> int:
        """
        Rollback all unrestored changes

        Args:
            reason: Reason for rollback

        Returns:
            Number of changes rolled back
        """
        entries = await self.get_all_unrestored_entries()
        if not entries:
            logging.info("No changes to rollback")
            return 0

        # Group by torrent hash and get most recent original limit
        original_limits = {}
        for entry in reversed(entries):  # Process oldest first
            if entry.torrent_hash not in original_limits:
                original_limits[entry.torrent_hash] = entry.old_limit

        logging.info(f"Rolling back {len(original_limits)} torrents to original limits")

        # We'll need the qBittorrent client to actually apply the rollback
        # For now, just mark as restored and return count
        # The actual limit restoration should be done by the caller

        restored_count = await self.mark_entries_restored(list(original_limits.keys()))
        self.stats["rollbacks_performed"] = (
            self.stats["rollbacks_performed"] or 0
        ) + restored_count
        self.stats["last_rollback_time"] = time.time()

        return restored_count

    async def get_rollback_data_for_application(self) -> Dict[str, int]:
        """
        Get the original limits that should be restored

        Returns:
            Dict mapping torrent_hash to original limit
        """
        entries = await self.get_all_unrestored_entries()

        # Group by torrent hash and get most recent original limit
        original_limits = {}
        for entry in reversed(entries):  # Process oldest first to get original limits
            if entry.torrent_hash not in original_limits:
                original_limits[entry.torrent_hash] = entry.old_limit

        return original_limits

    async def cleanup_old_entries(self, days_old: int = 30) -> int:
        """Remove old rollback entries"""
        cutoff_time = time.time() - (days_old * 24 * 3600)

        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                result = await db.execute(
                    """
                    DELETE FROM rollback_entries
                    WHERE timestamp < ? AND restored = 1
                """,
                    (cutoff_time,),
                )

                await db.commit()
                deleted = result.rowcount

                if deleted > 0:
                    logging.info(f"Cleaned up {deleted} old rollback entries")

                await self._update_db_size_stat()
                return deleted

        except Exception as e:
            logging.error(f"Failed to cleanup old entries: {e}")
            return 0

    async def get_rollback_stats(self) -> Dict[str, Any]:
        """Get rollback system statistics"""
        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                # Count total entries
                async with db.execute(
                    "SELECT COUNT(*) FROM rollback_entries"
                ) as cursor:
                    total_entries = (await cursor.fetchone())[0]

                # Count unrestored entries
                async with db.execute(
                    "SELECT COUNT(*) FROM rollback_entries WHERE restored = 0"
                ) as cursor:
                    unrestored_entries = (await cursor.fetchone())[0]

                # Get oldest unrestored entry
                oldest_unrestored = None
                async with db.execute(
                    """
                    SELECT MIN(timestamp) FROM rollback_entries WHERE restored = 0
                """
                ) as cursor:
                    result = await cursor.fetchone()
                    if result[0]:
                        oldest_unrestored = result[0]

                stats = self.stats.copy()
                stats.update(
                    {
                        "total_entries": total_entries,
                        "unrestored_entries": unrestored_entries,
                        "oldest_unrestored_timestamp": oldest_unrestored,
                        "database_path": str(self.db_path),
                    }
                )

                return stats

        except Exception as e:
            logging.error(f"Failed to get rollback stats: {e}")
            return self.stats.copy()

    async def _update_db_size_stat(self):
        """Update database size statistic"""
        try:
            if self.db_path.exists():
                size_bytes = self.db_path.stat().st_size
                self.stats["database_size_mb"] = round(size_bytes / (1024 * 1024), 2)
        except Exception:
            pass  # Ignore errors

    async def export_rollback_data(self, output_path: Path) -> bool:
        """Export rollback data to JSON file"""
        try:
            entries = await self.get_all_unrestored_entries()

            export_data = {
                "export_timestamp": time.time(),
                "total_entries": len(entries),
                "entries": [entry.to_dict() for entry in entries],
            }

            with open(output_path, "w") as f:
                json.dump(export_data, f, indent=2)

            logging.info(f"Exported {len(entries)} rollback entries to {output_path}")
            return True

        except Exception as e:
            logging.error(f"Failed to export rollback data: {e}")
            return False

    async def vacuum_database(self):
        """Optimize database (reduce file size)"""
        try:
            async with aiosqlite.connect(str(self.db_path)) as db:
                await db.execute("VACUUM")
                await db.commit()

            await self._update_db_size_stat()
            logging.info("Database vacuum completed")

        except Exception as e:
            logging.error(f"Database vacuum failed: {e}")
