"""Extra endpoint coverage for root and preview fallback."""

from fastapi.testclient import TestClient


def test_root_lists_new_endpoints():
    import src.main as main

    client = TestClient(main.app)
    r = client.get("/")
    assert r.status_code == 200
    eps = r.json()["endpoints"]
    # Newly added endpoints are present
    assert "/preview/next-cycle" in eps.values()
    assert "/smoothing/reset" in eps.values()
    assert "/stats/trackers" in eps.values()


def test_preview_fallback_when_no_method(monkeypatch):
    import src.main as main

    class Dummy:
        pass  # no preview_next_cycle method

    main.app_state["allocation_engine"] = Dummy()
    client = TestClient(main.app)
    r = client.get("/preview/next-cycle")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "unimplemented"
