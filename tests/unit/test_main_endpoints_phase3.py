"""Tests for Phase 3 preview endpoint."""

from fastapi.testclient import TestClient


def test_preview_next_cycle_endpoint(monkeypatch):
    import src.main as main

    client = TestClient(main.app)

    # Inject a dummy engine with preview method
    class DummyEngine:
        async def preview_next_cycle(self):
            return {
                "torrents_considered": 3,
                "proposed_changes": {"a": 1000, "b": 2000},
                "trackers": {"X": {"effective_cap": 123456}},
            }

    main.app_state["allocation_engine"] = DummyEngine()

    r = client.get("/preview/next-cycle")
    assert r.status_code == 200
    data = r.json()
    assert data["torrents_considered"] == 3
    assert set(data["proposed_changes"].keys()) == {"a", "b"}


def test_preview_next_cycle_real_engine(
    monkeypatch,
    test_config,
    mock_qbit_client,
    mock_tracker_matcher,
    mock_rollback_manager,
    sample_torrents,
):
    from fastapi.testclient import TestClient

    import src.main as main
    from src.allocation import AllocationEngine

    test_config.global_settings.allocation_strategy = "soft"

    engine = AllocationEngine(
        config=test_config,
        qbit_client=mock_qbit_client,
        tracker_matcher=mock_tracker_matcher,
        rollback_manager=mock_rollback_manager,
    )

    mock_qbit_client.get_torrents.return_value = sample_torrents

    # Inject engine and config
    main.app_state["allocation_engine"] = engine
    main.app_state["config"] = test_config

    client = TestClient(main.app)
    r = client.get("/preview/next-cycle")
    assert r.status_code == 200
    data = r.json()
    assert data["strategy"] == "soft"
    assert data["torrents_considered"] > 0
    assert data["proposed_count"] >= 1
    assert "summary" in data
    tops = data["summary"]["top_changes"]
    if tops:
        assert "delta_kib" in tops[0]
        assert isinstance(tops[0].get("new_limit_h"), str)
        if tops[0].get("delta_kib") is not None:
            assert isinstance(tops[0].get("delta_h"), str)

    # Tracker humanized strings
    ts = data["summary"]["trackers"]
    if ts:
        assert "base_cap_h" in ts[0]
