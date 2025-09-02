"""Error-path tests for RollbackManager to improve coverage."""

import builtins
import pytest

from src.rollback import RollbackManager
from src.config import RollbackSettings


@pytest.fixture
async def mgr(tmp_path):
    m = RollbackManager(RollbackSettings(database_path=str(tmp_path / "err.db"), track_all_changes=True))
    await m.initialize()
    return m


@pytest.mark.asyncio
async def test_record_change_error(monkeypatch, mgr: RollbackManager):
    async def boom_connect(*args, **kwargs):
        raise RuntimeError("db fail")

    monkeypatch.setattr("src.rollback.aiosqlite.connect", boom_connect)
    ok = await mgr.record_change("h", 1, 2, "t")
    assert ok is False


@pytest.mark.asyncio
async def test_record_batch_changes_error(monkeypatch, mgr: RollbackManager):
    async def boom_connect(*args, **kwargs):
        raise RuntimeError("db fail")

    monkeypatch.setattr("src.rollback.aiosqlite.connect", boom_connect)
    count = await mgr.record_batch_changes([("h", 1, 2, "t", "r")])
    assert count == 0


@pytest.mark.asyncio
async def test_get_entries_error(monkeypatch, mgr: RollbackManager):
    async def boom_connect(*args, **kwargs):
        raise RuntimeError("db fail")

    monkeypatch.setattr("src.rollback.aiosqlite.connect", boom_connect)
    out = await mgr.get_rollback_entries_for_torrent("h")
    assert out == []


@pytest.mark.asyncio
async def test_get_unrestored_error(monkeypatch, mgr: RollbackManager):
    async def boom_connect(*args, **kwargs):
        raise RuntimeError("db fail")

    monkeypatch.setattr("src.rollback.aiosqlite.connect", boom_connect)
    out = await mgr.get_all_unrestored_entries()
    assert out == []


@pytest.mark.asyncio
async def test_mark_entries_restored_error(monkeypatch, mgr: RollbackManager):
    async def boom_connect(*args, **kwargs):
        raise RuntimeError("db fail")

    monkeypatch.setattr("src.rollback.aiosqlite.connect", boom_connect)
    count = await mgr.mark_entries_restored(["a", "b"])
    assert count == 0


@pytest.mark.asyncio
async def test_cleanup_old_entries_error(monkeypatch, mgr: RollbackManager):
    async def boom_connect(*args, **kwargs):
        raise RuntimeError("db fail")

    monkeypatch.setattr("src.rollback.aiosqlite.connect", boom_connect)
    deleted = await mgr.cleanup_old_entries(days_old=1)
    assert deleted == 0


@pytest.mark.asyncio
async def test_get_rollback_stats_error(monkeypatch, mgr: RollbackManager):
    async def boom_connect(*args, **kwargs):
        raise RuntimeError("db fail")

    monkeypatch.setattr("src.rollback.aiosqlite.connect", boom_connect)
    stats = await mgr.get_rollback_stats()
    # Should return a copy of last known stats
    assert "database_size_mb" in stats


@pytest.mark.asyncio
async def test_export_rollback_data_error(monkeypatch, mgr: RollbackManager, tmp_path):
    def bad_open(*args, **kwargs):
        raise OSError("io fail")

    monkeypatch.setattr(builtins, "open", bad_open)
    ok = await mgr.export_rollback_data(tmp_path / "out.json")
    assert ok is False


@pytest.mark.asyncio
async def test_vacuum_database_error(monkeypatch, mgr: RollbackManager):
    async def boom_connect(*args, **kwargs):
        raise RuntimeError("db fail")

    monkeypatch.setattr("src.rollback.aiosqlite.connect", boom_connect)
    # Should not raise
    await mgr.vacuum_database()

