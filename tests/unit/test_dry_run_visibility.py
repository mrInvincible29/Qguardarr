"""Ensure dry_run visibility in /health and /stats endpoints."""

from fastapi.testclient import TestClient


def test_health_includes_dry_run(monkeypatch):
    import src.main as main

    class G:
        rollout_percentage = 42
        update_interval = 300
        dry_run = True

    class C:
        global_settings = G()

    main.app_state["config"] = C()

    client = TestClient(main.app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("dry_run") is True


def test_stats_includes_dry_run(monkeypatch):
    import src.main as main

    class DummyEngine:
        def get_detailed_stats(self):
            return {"dry_run": True}

    main.app_state["allocation_engine"] = DummyEngine()
    client = TestClient(main.app)
    r = client.get("/stats")
    assert r.status_code == 200
    assert r.json().get("dry_run") is True
