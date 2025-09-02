"""Cover main endpoint error paths (503/500)."""

from fastapi.testclient import TestClient


def test_preview_503_when_engine_missing():
    import src.main as main

    main.app_state["allocation_engine"] = None
    client = TestClient(main.app)
    r = client.get("/preview/next-cycle")
    assert r.status_code == 503


def test_smoothing_reset_503_when_engine_missing():
    import src.main as main

    main.app_state["allocation_engine"] = None
    client = TestClient(main.app)
    r = client.post("/smoothing/reset", json={"all": True})
    assert r.status_code == 503


def test_preview_500_when_engine_raises(monkeypatch):
    import src.main as main

    class BadEngine:
        async def preview_next_cycle(self):
            raise RuntimeError("boom")

    main.app_state["allocation_engine"] = BadEngine()
    client = TestClient(main.app)
    r = client.get("/preview/next-cycle")
    assert r.status_code == 500


def test_smoothing_reset_invalid_json(monkeypatch):
    import src.main as main

    class Dummy:
        def reset_smoothing(self, tracker_id=None):
            return 0

    main.app_state["allocation_engine"] = Dummy()

    # Ensure config is present so strategy is computed
    class G:
        allocation_strategy = "equal"

    class C:
        global_settings = G()

    main.app_state["config"] = C()

    client = TestClient(main.app)
    # Send invalid JSON body to trigger parsing except path
    r = client.post(
        "/smoothing/reset",
        data="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
