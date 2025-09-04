from fastapi.testclient import TestClient


def test_match_test_endpoint(monkeypatch):
    import src.main as main
    from src.config import TrackerConfig

    class DummyMatcher:
        def __init__(self):
            self.called = False

        def test_pattern_match(self, url: str, detailed: bool = False):
            self.called = True
            # Simulate match id
            return {"url": url, "matched_tracker": "default", "matches": []}

    main.app_state["tracker_matcher"] = DummyMatcher()
    client = TestClient(main.app)
    r = client.get("/match/test", params={"url": "http://example.com/announce"})
    assert r.status_code == 200
    data = r.json()
    assert data["matched_tracker"] == "default"
