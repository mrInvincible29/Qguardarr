"""Unit tests for AllocationEngine.preview_next_cycle (Phase 3)."""

import pytest

from src.allocation import AllocationEngine


@pytest.mark.asyncio
async def test_preview_next_cycle_soft_strategy(
    test_config,
    mock_qbit_client,
    mock_tracker_matcher,
    mock_rollback_manager,
    sample_torrents,
):
    # Enable soft strategy
    test_config.global_settings.allocation_strategy = "soft"

    # Provide sample torrents
    mock_qbit_client.get_torrents.return_value = sample_torrents

    engine = AllocationEngine(
        config=test_config,
        qbit_client=mock_qbit_client,
        tracker_matcher=mock_tracker_matcher,
        rollback_manager=mock_rollback_manager,
    )

    # No smoothing state should be mutated by preview
    assert engine._last_effective_caps == {}

    preview = await engine.preview_next_cycle()

    assert preview["strategy"] == "soft"
    assert preview["torrents_considered"] > 0
    assert preview["proposed_count"] >= 1
    assert isinstance(preview["proposed_changes"], dict)
    assert "trackers" in preview
    # UI summary present
    assert "summary" in preview
    assert isinstance(preview["summary"].get("trackers"), list)
    # Either top changes present or none when no proposed
    assert "top_changes" in preview["summary"]
    tops = preview["summary"]["top_changes"]
    if tops:
        assert "hash" in tops[0]
        assert "new_limit_kib" in tops[0]
        assert "delta_kib" in tops[0]
        assert isinstance(tops[0].get("new_limit_h"), str)
        # delta_h may be None when current is unknown
        if tops[0].get("delta_kib") is not None:
            assert isinstance(tops[0].get("delta_h"), str)

    # Humanized strings for tracker summary
    ts = preview["summary"]["trackers"]
    if ts:
        assert "base_cap_h" in ts[0]
        # effective/borrowed may be None/0 for some trackers; when present ensure string
        if ts[0].get("effective_cap_mbps"):
            assert isinstance(ts[0].get("effective_cap_h"), str)

    # Check trackers info present for configured trackers
    trackers = preview["trackers"]
    assert set(trackers.keys()).issuperset(
        {"test_tracker1", "test_tracker2", "default"}
    )

    # Ensure base caps are correct
    assert trackers["test_tracker1"]["base_cap"] == 5 * 1024 * 1024
    assert trackers["test_tracker2"]["base_cap"] == 2 * 1024 * 1024

    # Smoothing state should remain unchanged after preview
    assert engine._last_effective_caps == {}
