"""Application-level rollback apply tests using TestClient and real RollbackManager."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config import (
    CrossSeedSettings,
    GlobalSettings,
    LoggingSettings,
    QBittorrentSettings,
    QguardarrConfig,
    RollbackSettings,
    TrackerConfig,
)
from src.rollback import RollbackManager


@pytest.fixture
def app_with_rollback(tmp_path):
    """Spin up the FastAPI app with a real rollback DB and mocked qbit client."""
    import src.main as main

    # Minimal config
    cfg = QguardarrConfig(
        **{
            "global": GlobalSettings(
                update_interval=300,
                active_torrent_threshold_kb=10,
                max_api_calls_per_cycle=500,
                differential_threshold=0.2,
                rollout_percentage=100,
                host="127.0.0.1",
                port=8089,
            ),
            "qbittorrent": QBittorrentSettings(
                host="localhost",
                port=8080,
                username="admin",
                password="secret",
                timeout=15,
            ),
            "cross_seed": CrossSeedSettings(
                enabled=False, url=None, api_key=None, timeout=15
            ),
            "trackers": [
                TrackerConfig(
                    id="default",
                    name="Default",
                    pattern=".*",
                    max_upload_speed=1024 * 1024,
                    priority=1,
                )
            ],
            "rollback": RollbackSettings(
                database_path=str(tmp_path / "rollback.db"), track_all_changes=True
            ),
            "logging": LoggingSettings(
                level="INFO",
                file=str(tmp_path / "test.log"),
                max_size_mb=10,
                backup_count=1,
            ),
        }
    )

    class DummyConfigLoader:
        def load_config(self):
            return cfg

    # Patch config loader
    main.ConfigLoader = DummyConfigLoader  # type: ignore

    # Real rollback manager with temp DB
    rb = RollbackManager(cfg.rollback)

    # Mock qbit client to capture batch updates
    class CaptureQbit:
        def __init__(self):
            self.batches = []

        async def set_torrents_upload_limits_batch(self, limits):
            self.batches.append(limits.copy())

    qbit = CaptureQbit()

    # Build app client
    client = TestClient(main.app)

    # Ensure app_state has our instances
    main.app_state["config"] = cfg
    main.app_state["rollback_manager"] = rb
    main.app_state["qbit_client"] = qbit

    # Initialize rollback DB
    asyncio.get_event_loop().run_until_complete(rb.initialize())

    return client, rb, qbit


@pytest.mark.asyncio
async def test_rollback_applies_original_limits_and_unlimited(app_with_rollback):
    client, rb, qbit = app_with_rollback

    # Seed rollback entries: two torrents originally unlimited (-1), one finite
    # Format expected by record_batch_changes: (hash, old_limit, new_limit, tracker_id, reason)
    changes = [
        ("h_unlim1", -1, 2000000, "default", "alloc"),
        ("h_unlim2", -1, 1500000, "default", "alloc"),
        ("h_finite", 500000, 250000, "default", "alloc"),
    ]
    count = await rb.record_batch_changes(changes)
    assert count == 3

    # Call rollback endpoint
    r = client.post("/rollback", json={"confirm": True, "reason": "test"})
    assert r.status_code == 200
    data = r.json()
    assert data["changes_reversed"] == 3

    # Verify qbit batch was called with original limits
    assert len(qbit.batches) >= 1
    # Merge batches if multiple
    merged = {}
    for b in qbit.batches:
        merged.update(b)

    assert merged["h_unlim1"] == -1
    assert merged["h_unlim2"] == -1
    assert merged["h_finite"] == 500000
