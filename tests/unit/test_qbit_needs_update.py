"""Unit tests for QBittorrentClient.needs_update thresholds."""

from src.config import QBittorrentSettings
from src.qbit_client import QBittorrentClient


def _client() -> QBittorrentClient:
    return QBittorrentClient(
        QBittorrentSettings(host="localhost", port=8080, username="u", password="p")
    )


def test_needs_update_small_speeds_absolute():
    c = _client()
    # Below 50 KiB/s range: update only if >10 KiB/s change
    assert c.needs_update(30 * 1024, 35 * 1024) is False
    assert c.needs_update(30 * 1024, 41 * 1024) is True


def test_needs_update_medium_combined():
    c = _client()
    # <1 MiB/s range: update if >50 KiB abs OR >30% rel
    # Small absolute and low relative
    assert c.needs_update(400 * 1024, 420 * 1024) is False
    # Absolute > 50 KiB
    assert c.needs_update(400 * 1024, 460 * 1024) is True
    # Relative > 30%
    assert c.needs_update(100 * 1024, 140 * 1024) is True


def test_needs_update_high_speeds_both():
    c = _client()
    # >1 MiB/s: need >100 KiB abs AND > threshold rel (default 0.2)
    # abs ok but rel small
    assert (
        c.needs_update(2 * 1024 * 1024, 2 * 1024 * 1024 + 120 * 1024, threshold=0.2)
        is False
    )
    # abs and rel ok
    assert (
        c.needs_update(2 * 1024 * 1024, 2 * 1024 * 1024 + 300 * 1024, threshold=0.1)
        is True
    )


def test_needs_update_unlimited_crossing():
    c = _client()
    # Crossing unlimited boundary always updates
    assert c.needs_update(-1, 100 * 1024) is True
    assert c.needs_update(100 * 1024, -1) is True
    # Staying unlimited on both sides: no update
    assert c.needs_update(-1, -1) is False
