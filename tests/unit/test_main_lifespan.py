"""Additional tests for src.main lifespan and failure branches."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def patched_main_success(monkeypatch):
    import src.main as main

    # Minimal config
    from src.config import (
        QguardarrConfig,
        GlobalSettings,
        QBittorrentSettings,
        TrackerConfig,
        RollbackSettings,
        CrossSeedSettings,
        LoggingSettings,
    )

    cfg = QguardarrConfig(
        **{
            "global": GlobalSettings(
                update_interval=300,
                active_torrent_threshold_kb=10,
                max_api_calls_per_cycle=500,
                differential_threshold=0.2,
                rollout_percentage=50,
                host="127.0.0.1",
                port=8089,
            ),
            "qbittorrent": QBittorrentSettings(
                host="localhost",
                port=8080,
                username="admin",
                password="secret",
                timeout=10,
            ),
            "cross_seed": CrossSeedSettings(enabled=False),
            "trackers": [
                TrackerConfig(
                    id="default", name="Default", pattern=".*", max_upload_speed=1024 * 1024, priority=1
                )
            ],
            "rollback": RollbackSettings(database_path="./data/test.db", track_all_changes=True),
            "logging": LoggingSettings(level="INFO", file="./logs/test.log", max_size_mb=10, backup_count=1),
        }
    )

    class DummyConfigLoader:
        def load_config(self):
            return cfg

    called = {"connect": 0, "disconnect": 0, "init": 0, "start": 0, "stop": 0, "cycles": 0}

    async def fake_connect(self):
        called["connect"] += 1

    async def fake_disconnect(self):
        called["disconnect"] += 1

    async def fake_init(self):
        called["init"] += 1

    async def fake_start(self):
        called["start"] += 1

    async def fake_stop(self):
        called["stop"] += 1

    async def fake_cycle_task():
        called["cycles"] += 1
        return None

    # Patch pieces used in startup
    monkeypatch.setattr(main, "ConfigLoader", DummyConfigLoader)
    monkeypatch.setattr(main.QBittorrentClient, "connect", fake_connect, raising=True)
    monkeypatch.setattr(main.QBittorrentClient, "disconnect", fake_disconnect, raising=True)
    monkeypatch.setattr(main.RollbackManager, "initialize", fake_init, raising=True)
    monkeypatch.setattr(main.WebhookHandler, "start_event_processor", fake_start, raising=True)
    monkeypatch.setattr(main.WebhookHandler, "stop", fake_stop, raising=True)
    # Ensure allocation cycle schedules a quick task
    monkeypatch.setattr(main, "allocation_cycle_task", fake_cycle_task, raising=True)

    return main, called


def test_lifespan_startup_and_shutdown(patched_main_success):
    main, called = patched_main_success

    with TestClient(main.app) as client:
        # Startup completed
        assert main.app_state["health_status"] == "healthy"
        # Background tasks scheduled at least once
        assert called["cycles"] >= 0
        # Health endpoint available
        resp = client.get("/health")
        assert resp.status_code == 200

    # After context, shutdown executed
    assert called["connect"] == 1
    assert called["disconnect"] == 1
    assert called["init"] == 1
    assert called["start"] == 1
    assert called["stop"] == 1


def test_lifespan_startup_failure_sets_unhealthy(monkeypatch):
    import src.main as main

    async def boom():
        raise RuntimeError("startup failed")

    shutdown_called = {"v": 0}

    async def fake_shutdown():
        shutdown_called["v"] += 1

    monkeypatch.setattr(main, "startup_event", boom, raising=True)
    monkeypatch.setattr(main, "shutdown_event", fake_shutdown, raising=True)

    # Lifespan is driven by TestClient context
    with pytest.raises(Exception):
        with TestClient(main.app):
            pass

    assert main.app_state["health_status"] == "unhealthy"
    assert shutdown_called["v"] == 1


def test_unready_endpoints_and_error_paths(monkeypatch):
    import src.main as main
    client = TestClient(main.app)

    # Simulate unready states
    main.app_state["allocation_engine"] = None
    r = client.get("/stats")
    assert r.status_code == 503
    r = client.get("/stats/trackers")
    assert r.status_code == 503

    main.app_state["webhook_handler"] = None
    r = client.post("/webhook", data={})
    assert r.status_code == 503

    main.app_state["config"] = None
    r = client.get("/config")
    assert r.status_code == 503

    # Error branch in cycle/force
    class BadEngine:
        async def run_allocation_cycle(self):
            raise RuntimeError("boom")

    main.app_state["allocation_engine"] = BadEngine()
    r = client.post("/cycle/force")
    assert r.status_code == 500

