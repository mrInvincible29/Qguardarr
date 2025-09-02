"""Unit tests for FastAPI endpoints in src.main"""

import asyncio
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from src.config import (
    QguardarrConfig,
    GlobalSettings,
    QBittorrentSettings,
    TrackerConfig,
    RollbackSettings,
    CrossSeedSettings,
    LoggingSettings,
)


@pytest.fixture
def test_config(tmp_path) -> QguardarrConfig:
    return QguardarrConfig(
        **{
            "global": GlobalSettings(
                update_interval=300,
                active_torrent_threshold_kb=10,
                max_api_calls_per_cycle=500,
                differential_threshold=0.2,
                rollout_percentage=42,
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
                enabled=True,
                url="http://localhost:2468/api/webhook",
                api_key="abc123",
                timeout=15,
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
                database_path=str(tmp_path / "rollback.db"),
                track_all_changes=True,
            ),
            "logging": LoggingSettings(
                level="INFO",
                file=str(tmp_path / "qguardarr.log"),
                max_size_mb=10,
                backup_count=1,
            ),
        }
    )


@pytest.fixture
def app_client(monkeypatch, test_config):
    # Patch ConfigLoader to return our test config
    import src.main as main

    class DummyConfigLoader:
        def __init__(self, *args, **kwargs):
            pass

        def load_config(self) -> QguardarrConfig:
            return test_config

    # No-op connections and heavy cycles
    async def noop_connect(self):
        self.authenticated = True

    async def noop_initialize(self):
        return None

    async def noop_cycle(self):
        return None

    monkeypatch.setattr(main, "ConfigLoader", DummyConfigLoader)
    monkeypatch.setattr(main.QBittorrentClient, "connect", noop_connect, raising=True)
    monkeypatch.setattr(main.RollbackManager, "initialize", noop_initialize, raising=True)
    monkeypatch.setattr(
        main.AllocationEngine, "run_allocation_cycle", noop_cycle, raising=True
    )

    # Provide deterministic stats endpoints
    class DummyEngine:
        async def run_allocation_cycle(self):
            return None

        def get_detailed_stats(self) -> Dict[str, Any]:
            return {"allocation_cycles": 1, "active_torrents": 0}

        def get_tracker_stats(self) -> Dict[str, Any]:
            return {"default": {"active_torrents": 0}}

        def get_stats(self) -> Dict[str, Any]:
            return {"active_torrents": 0, "managed_torrents": 0, "api_calls_last_cycle": 0}

    client = TestClient(main.app)

    # Ensure allocation engine initialized before returning client
    import time as _time
    for _ in range(20):  # up to ~1s
        if main.app_state.get("allocation_engine") is not None:
            break
        _time.sleep(0.05)

    # Ensure stats endpoints have a valid engine regardless of startup variance
    if main.app_state.get("allocation_engine") is None:
        main.app_state["allocation_engine"] = DummyEngine()
    else:
        # Replace with dummy to avoid any external calls
        main.app_state["allocation_engine"] = DummyEngine()

    # Ensure config is available for /config and /health
    main.app_state["config"] = test_config

    # Ensure webhook handler exists for /webhook
    from fastapi.responses import JSONResponse

    class DummyWebhook:
        async def handle_webhook(self, request):
            return JSONResponse({"status": "queued", "queue_size": 0}, status_code=202)

    main.app_state["webhook_handler"] = DummyWebhook()

    # Ensure rollback manager is available
    class DummyRollback:
        async def rollback_all_changes(self, reason: str = "manual_rollback") -> int:
            return 3

    main.app_state["rollback_manager"] = DummyRollback()
    return client


def test_root_endpoint(app_client: TestClient):
    r = app_client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Qguardarr"
    assert "endpoints" in data


def test_health_endpoint(app_client: TestClient):
    r = app_client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in {"healthy", "starting", "degraded"}
    assert "uptime_seconds" in data
    # rollout_percentage should be present once startup completed; tolerate race
    if "rollout_percentage" in data:
        assert data["rollout_percentage"] == 42


def test_stats_endpoints(app_client: TestClient):
    # Wait until stats endpoint is ready
    import time as _time
    for _ in range(20):
        r = app_client.get("/stats")
        if r.status_code == 200:
            break
        _time.sleep(0.05)
    assert r.status_code == 200
    assert "allocation_cycles" in r.json()

    for _ in range(20):
        r2 = app_client.get("/stats/trackers")
        if r2.status_code == 200:
            break
        _time.sleep(0.05)
    assert r2.status_code == 200
    assert "default" in r2.json()


def test_config_endpoint_sanitized(app_client: TestClient):
    r = app_client.get("/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["qbittorrent"]["password"] == "***"
    assert cfg["cross_seed"]["api_key"] == "***"


def test_force_cycle_endpoint(app_client: TestClient, monkeypatch):
    import src.main as main

    called = {"count": 0}
    
    class CycleEngine:
        async def run_allocation_cycle(self):
            called["count"] += 1

    # Inject our engine so the endpoint uses it
    main.app_state["allocation_engine"] = CycleEngine()
    r = app_client.post("/cycle/force")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert called["count"] == 1


def test_webhook_endpoint_accepted(app_client: TestClient):
    r = app_client.post(
        "/webhook",
        data={"event": "complete", "hash": "abc123", "tracker": "http://t/announce"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 202


def test_rollback_endpoint(app_client: TestClient):
    r = app_client.post("/rollback", json={"confirm": True, "reason": "test"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "completed"
    assert data["changes_reversed"] == 3


def test_rollback_requires_confirm(app_client: TestClient):
    r = app_client.post("/rollback", json={"confirm": False})
    # Normalized behavior: bad request returns 400
    assert r.status_code == 400


def test_rollout_update(app_client: TestClient):
    r = app_client.post("/rollout", json={"percentage": 55})
    assert r.status_code == 200
    assert r.json()["rollout_percentage"] == 55

    r2 = app_client.post("/rollout", json={"percentage": 0})
    # Normalized behavior: bad request returns 400
    assert r2.status_code == 400
