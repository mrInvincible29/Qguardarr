"""Tests for auto-unlimit behavior when torrents become unmanaged."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


def _make_torrent(hash_: str, upspeed: int = 0) -> TorrentInfo:
    return TorrentInfo(
        hash=hash_,
        name=hash_,
        state="uploading",
        progress=1.0,
        dlspeed=0,
        upspeed=upspeed,
        priority=1,
        num_seeds=5,
        num_leechs=5,
        ratio=1.0,
        size=1000,
        completed=1000,
        tracker="http://t/announce",
    )


@pytest.mark.asyncio
async def test_auto_unlimit_unmanaged_real_mode(test_config: QguardarrConfig):
    # Enable auto-unlimit on unmanaged
    test_config.global_settings.auto_unlimit_on_inactive = True  # type: ignore[attr-defined]

    qbit = AsyncMock()
    # Cycle 1: one active torrent
    h1 = _make_torrent("h1", upspeed=200 * 1024)
    # Cycle 2: no active torrents
    qbit.get_torrents.side_effect = [[h1], []]
    qbit.get_torrent_upload_limit.return_value = 250_000
    qbit.set_torrents_upload_limits_batch = AsyncMock()
    qbit.needs_update.return_value = True

    matcher = Mock()
    matcher.match_tracker.return_value = "default"
    matcher.get_tracker_config.return_value = Mock(max_upload_speed=1 * 1024 * 1024)

    rollback = AsyncMock()
    rollback.record_batch_changes = AsyncMock(return_value=1)

    engine = AllocationEngine(test_config, qbit, matcher, rollback)

    # First cycle: apply a limit to h1
    await engine.run_allocation_cycle()

    # Second cycle: h1 unmanaged -> should be set to -1
    await engine.run_allocation_cycle()

    # Verify the last call un-limited h1
    args, _ = qbit.set_torrents_upload_limits_batch.await_args
    assert args and args[0].get("h1") == -1


@pytest.mark.asyncio
async def test_auto_unlimit_unmanaged_dry_run(
    tmp_path: Path, test_config: QguardarrConfig
):
    # Enable dry run and auto-unlimit
    test_config.global_settings.dry_run = True
    test_config.global_settings.dry_run_store_path = str(tmp_path / "dry.json")
    test_config.global_settings.auto_unlimit_on_inactive = True  # type: ignore[attr-defined]

    qbit = AsyncMock()
    h1 = _make_torrent("hx", upspeed=150 * 1024)
    qbit.get_torrents.side_effect = [[h1], []]
    qbit.get_torrent_upload_limit.return_value = 100_000
    qbit.needs_update.return_value = True

    matcher = Mock()
    matcher.match_tracker.return_value = "default"
    matcher.get_tracker_config.return_value = Mock(max_upload_speed=1 * 1024 * 1024)

    rollback = AsyncMock()

    engine = AllocationEngine(test_config, qbit, matcher, rollback)

    # Cycle to add and then unmanage the torrent
    await engine.run_allocation_cycle()
    await engine.run_allocation_cycle()

    # In dry-run, no writes to qBittorrent should occur
    assert qbit.set_torrents_upload_limits_batch.await_count == 0

    # The dry-run store should record unlimited for the unmanaged torrent
    store_path = Path(test_config.global_settings.dry_run_store_path)
    assert store_path.exists()
    import json

    data = json.loads(store_path.read_text())
    assert data.get("hx") == -1
