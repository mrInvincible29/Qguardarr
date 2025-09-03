"""Tests for /limits/reset endpoint in dry-run and real modes."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


def test_limits_reset_dry_run(tmp_path: Path, monkeypatch):
    import src.main as main
    from src.allocation import AllocationEngine
    from src.config import QguardarrConfig, GlobalSettings, QBittorrentSettings, RollbackSettings, CrossSeedSettings, LoggingSettings, TrackerConfig

    cfg = QguardarrConfig(
        **{
            "global": GlobalSettings(dry_run=True, dry_run_store_path=str(tmp_path / "dry.json")),
            "qbittorrent": QBittorrentSettings(host="h", port=8080, username="u", password="p"),
            "cross_seed": CrossSeedSettings(enabled=False),
            "trackers": [TrackerConfig(id="default", name="d", pattern=".*", max_upload_speed=-1, priority=1)],
            "rollback": RollbackSettings(database_path=str(tmp_path / "rb.db"), track_all_changes=True),
            "logging": LoggingSettings(),
        }
    )

    qbit = AsyncMock()
    matcher = AsyncMock()
    rollback = AsyncMock()
    # Distinct hashes returned
    rollback.get_distinct_hashes.return_value = ["a", "b"]

    engine = AllocationEngine(config=cfg, qbit_client=qbit, tracker_matcher=matcher, rollback_manager=rollback)
    main.app_state["allocation_engine"] = engine
    main.app_state["rollback_manager"] = rollback
    main.app_state["qbit_client"] = qbit

    client = TestClient(main.app)
    r = client.post("/limits/reset", json={"confirm": True, "scope": "all"})
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "dry-run"
    # Store contains reset entries
    store = json.loads(Path(cfg.global_settings.dry_run_store_path).read_text())
    assert store["a"] == -1 and store["b"] == -1


def test_limits_reset_real(monkeypatch):
    import src.main as main

    class DummyEngine:
        dry_run = False
        cache = type("C", (), {"hash_to_index": {}, "current_limits": {}})()

    engine = DummyEngine()
    rollback = AsyncMock()
    rollback.get_distinct_hashes.return_value = ["h1", "h2"]
    qbit = AsyncMock()

    main.app_state["allocation_engine"] = engine
    main.app_state["rollback_manager"] = rollback
    main.app_state["qbit_client"] = qbit

    client = TestClient(main.app)
    r = client.post("/limits/reset", json={"confirm": True, "scope": "unrestored", "mark_restored": True})
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "real"
    # API called with -1
    args, _ = qbit.set_torrents_upload_limits_batch.await_args
    assert all(v == -1 for v in args[0].values())
    # Mark restored called with hashes
    hashes_arg, _ = rollback.mark_entries_restored.await_args
    assert set(hashes_arg[0]) == {"h1", "h2"}
