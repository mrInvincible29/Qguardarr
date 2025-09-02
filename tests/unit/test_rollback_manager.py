"""Unit tests for RollbackManager (SQLite)"""

import json
import time
from pathlib import Path

import pytest

from src.rollback import RollbackManager, RollbackEntry
from src.config import RollbackSettings


@pytest.fixture
async def rollback_mgr(tmp_path):
    db_path = tmp_path / "rb.db"
    mgr = RollbackManager(RollbackSettings(database_path=str(db_path), track_all_changes=True))
    await mgr.initialize()
    return mgr


@pytest.mark.asyncio
async def test_record_and_query(rollback_mgr: RollbackManager):
    ok = await rollback_mgr.record_change("hash1", -1, 1000, "t1", reason="test")
    assert ok is True

    entries = await rollback_mgr.get_rollback_entries_for_torrent("hash1")
    assert len(entries) == 1
    e = entries[0]
    assert isinstance(e, RollbackEntry)
    assert e.torrent_hash == "hash1"
    assert e.old_limit == -1 and e.new_limit == 1000


@pytest.mark.asyncio
async def test_unrestored_and_mark_restored(rollback_mgr: RollbackManager):
    # Add two entries across two torrents
    await rollback_mgr.record_change("h1", 1000, 2000, "t1")
    await rollback_mgr.record_change("h2", 500, 600, "t2")

    all_unrestored = await rollback_mgr.get_all_unrestored_entries()
    assert {e.torrent_hash for e in all_unrestored} == {"h1", "h2"}

    marked = await rollback_mgr.mark_entries_restored(["h1"])
    assert marked == 1

    remaining = await rollback_mgr.get_all_unrestored_entries()
    assert {e.torrent_hash for e in remaining} == {"h2"}


@pytest.mark.asyncio
async def test_rollback_all_changes(rollback_mgr: RollbackManager):
    await rollback_mgr.record_change("h1", 10, 20, "t1")
    await rollback_mgr.record_change("h1", 20, 30, "t1")
    await rollback_mgr.record_change("h2", -1, 100, "t2")

    count = await rollback_mgr.rollback_all_changes("manual")
    # Current implementation marks all rows restored; expect 3 rows updated
    assert count == 3

    # Subsequent call should see none pending
    count2 = await rollback_mgr.rollback_all_changes("manual")
    assert count2 == 0


@pytest.mark.asyncio
async def test_export_and_cleanup(rollback_mgr: RollbackManager, tmp_path, monkeypatch):
    # Create one old, restored entry and one recent unrestored
    old_time = time.time() - 40 * 24 * 3600  # 40 days ago

    # Monkeypatch time.time to create an old entry
    monkeypatch.setattr("time.time", lambda: old_time)
    await rollback_mgr.record_change("oldhash", 1, 2, "t1")
    await rollback_mgr.mark_entries_restored(["oldhash"])

    # New entry at current time
    monkeypatch.setattr("time.time", lambda: old_time + 40 * 24 * 3600)
    await rollback_mgr.record_change("newhash", 3, 4, "t2")

    # Export unrestored data (should include only newhash)
    out = tmp_path / "export.json"
    ok = await rollback_mgr.export_rollback_data(out)
    assert ok is True and out.exists()
    data = json.loads(out.read_text())
    hashes = {e["torrent_hash"] for e in data["entries"]}
    assert hashes == {"newhash"}

    # Cleanup should remove the old restored entry
    deleted = await rollback_mgr.cleanup_old_entries(days_old=30)
    assert deleted >= 1

    stats = await rollback_mgr.get_rollback_stats()
    assert "total_entries" in stats


@pytest.mark.asyncio
async def test_rollback_data_and_vacuum(rollback_mgr: RollbackManager):
    await rollback_mgr.record_change("z1", 111, 222, "t1")
    await rollback_mgr.record_change("z1", 222, 333, "t1")
    await rollback_mgr.record_change("z2", -1, 100, "t2")

    data = await rollback_mgr.get_rollback_data_for_application()
    # Current implementation uses the most recent old_limit (not the first)
    assert data["z1"] == 222
    assert data["z2"] == -1

    # Vacuum should run without error
    await rollback_mgr.vacuum_database()
