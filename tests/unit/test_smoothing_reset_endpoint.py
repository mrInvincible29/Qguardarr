"""Tests for smoothing reset endpoint and strategy in stats."""

from fastapi.testclient import TestClient


def test_smoothing_reset_endpoint(
    monkeypatch,
    test_config,
    mock_qbit_client,
    mock_tracker_matcher,
    mock_rollback_manager,
):
    import src.main as main
    from src.allocation import AllocationEngine

    engine = AllocationEngine(
        config=test_config,
        qbit_client=mock_qbit_client,
        tracker_matcher=mock_tracker_matcher,
        rollback_manager=mock_rollback_manager,
    )
    # Seed smoothing state for two trackers
    engine._last_effective_caps["test_tracker1"] = 5 * 1024 * 1024
    engine._last_effective_caps["test_tracker2"] = 2 * 1024 * 1024

    main.app_state["allocation_engine"] = engine

    client = TestClient(main.app)

    # Reset a single tracker
    r1 = client.post("/smoothing/reset", json={"tracker_id": "test_tracker1"})
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["cleared_count"] == 1
    assert data1.get("strategy") in {"equal", "weighted", "soft"}
    # Not soft by default in test_config -> message should be present
    assert "message" in data1
    assert "test_tracker1" not in engine._last_effective_caps

    # Reset all
    r2 = client.post("/smoothing/reset", json={"all": True})
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["cleared_count"] >= 1  # cleared remaining
    assert data2.get("strategy") in {"equal", "weighted", "soft"}
    assert engine._last_effective_caps == {}


def test_stats_includes_strategy(
    monkeypatch,
    test_config,
    mock_qbit_client,
    mock_tracker_matcher,
    mock_rollback_manager,
):
    from fastapi.testclient import TestClient

    import src.main as main
    from src.allocation import AllocationEngine

    # Set soft strategy
    test_config.global_settings.allocation_strategy = "soft"

    engine = AllocationEngine(
        config=test_config,
        qbit_client=mock_qbit_client,
        tracker_matcher=mock_tracker_matcher,
        rollback_manager=mock_rollback_manager,
    )

    main.app_state["allocation_engine"] = engine
    main.app_state["config"] = test_config

    client = TestClient(main.app)
    r = client.get("/stats")
    assert r.status_code == 200
    assert r.json().get("strategy") == "soft"
