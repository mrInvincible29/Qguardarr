"""Focused unit tests for qBittorrent client utilities (no network)"""

import asyncio
from typing import Any, Dict, List

import pytest

from src.config import QBittorrentSettings
from src.qbit_client import APICircuitBreaker, QBittorrentClient, TorrentInfo


def make_client() -> QBittorrentClient:
    cfg = QBittorrentSettings(
        host="localhost", port=8080, username="u", password="p", timeout=10
    )
    return QBittorrentClient(cfg)


class TestCircuitBreaker:
    def test_state_transitions(self):
        cb = APICircuitBreaker(failure_threshold=2, recovery_timeout=0)
        assert cb.can_execute() is True
        cb.on_failure()
        assert cb.state == "closed"
        cb.on_failure()
        assert cb.state == "open"
        # After timeout=0, can_execute moves to half-open
        assert cb.can_execute() is True
        assert cb.state == "half-open"
        cb.on_success()
        assert cb.state == "closed"


class TestTorrentInfoProps:
    def test_properties(self):
        t = TorrentInfo(
            hash="h",
            name="n",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=2048,
            priority=1,
            num_seeds=5,
            num_leechs=2,
            ratio=1.0,
            size=100,
            completed=100,
        )
        assert abs(t.upload_speed_kb - 2.0) < 1e-6
        assert t.is_active is True
        assert t.num_peers == 7


class TestNeedsUpdate:
    def test_thresholds_and_boundaries(self):
        client = make_client()

        # Crossing unlimited boundary
        assert client.needs_update(1000, -1) is True
        assert client.needs_update(-1, 1000) is True
        assert client.needs_update(-1, 0) is False  # both unlimited

        # Very small speeds: absolute threshold of 10KB/s
        assert client.needs_update(20000, 25000) is False  # 5KB diff
        assert client.needs_update(20000, 35000) is True  # 15KB diff

        # Medium speeds: abs > 50KB OR rel > 30%
        base = 800_000  # < 1MB/s
        assert client.needs_update(base, base + 30_000) is False
        assert client.needs_update(base, base + 60_000) is True  # abs > 50KB
        assert client.needs_update(base, int(base * 1.35)) is True  # rel > 30%

        # High speeds: abs > 100KB AND rel > threshold (default 0.2)
        base = 2_000_000
        assert client.needs_update(base, base + 50_000) is False
        assert client.needs_update(base, int(base * 1.05)) is False  # 5% < 20%
        assert client.needs_update(base, int(base * 1.25)) is True


@pytest.mark.asyncio
async def test_batch_grouping(monkeypatch):
    client = make_client()

    # Stub _make_request to record calls without network
    calls: List[Dict[str, Any]] = []

    async def fake_request(method: str, endpoint: str, **kwargs):
        calls.append({"endpoint": endpoint, "data": kwargs.get("data")})

        class Dummy:
            def raise_for_status(self):
                return None

        return Dummy()

    monkeypatch.setattr(client, "_make_request", fake_request)

    # Prepare limits with two groups and batching
    limits = {
        "h1": 1000,
        "h2": 1000,
        "h3": 2000,
    }

    await client.set_torrents_upload_limits_batch(limits, batch_size=2)

    # Expect two posts for 1000 group (batch of 2) and one for 2000 group
    posted = [c for c in calls if c["endpoint"] == "/api/v2/torrents/setUploadLimit"]
    assert len(posted) == 2  # one batch for 1000 (2 hashes), one for 2000 (1 hash)
    # Validate payloads contain expected limits
    payload_limits = sorted(p["data"]["limit"] for p in posted)
    assert payload_limits == [1000, 2000]
