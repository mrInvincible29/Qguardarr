import asyncio
import json

import pytest

from src.config import QBittorrentSettings
from src.qbit_client import QBittorrentClient


class DummyResp:
    def __init__(self, text="", status_code=200, json_data=None):
        self._text = text
        self.status_code = status_code
        self._json = json_data

    @property
    def text(self):
        return self._text

    def json(self):
        return self._json if self._json is not None else json.loads(self._text)

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.mark.asyncio
async def test_get_torrents_active_filters_and_limits_tracker_calls(monkeypatch):
    settings = QBittorrentSettings(
        host="localhost", port=8080, username="u", password="p", timeout=10
    )
    client = QBittorrentClient(settings)

    # Track calls
    calls = {"info": 0, "trackers": []}

    async def fake_make_request(self, method, endpoint, **kwargs):
        if endpoint.startswith("/api/v2/torrents/info"):
            calls["info"] += 1
            params = kwargs.get("params") or {}
            # Assert filter=active is used
            assert params.get("filter") == "active"
            # Return two torrents: one idle uploader, one active uploader
            data = [
                {
                    "hash": "A" * 40,
                    "name": "idle",
                    "state": "seeding",
                    "progress": 1.0,
                    "dlspeed": 0,
                    "upspeed": 0,
                    "priority": 0,
                    "num_seeds": 0,
                    "num_leechs": 0,
                    "ratio": 0.0,
                    "size": 1,
                    "completed": 1,
                },
                {
                    "hash": "B" * 40,
                    "name": "active",
                    "state": "seeding",
                    "progress": 1.0,
                    "dlspeed": 0,
                    "upspeed": 1024,  # > 0
                    "priority": 0,
                    "num_seeds": 0,
                    "num_leechs": 0,
                    "ratio": 0.0,
                    "size": 1,
                    "completed": 1,
                },
            ]
            return DummyResp(json_data=data)

        if endpoint.startswith("/api/v2/torrents/trackers"):
            # Only called for active torrent with hash 'B'*40
            params = kwargs.get("params") or {}
            calls["trackers"].append(params.get("hash"))
            return DummyResp(json_data=[{"status": 2, "url": "http://t/announce"}])

        return DummyResp(json_data={})

    # Patch internal request method and skip connect
    monkeypatch.setattr(QBittorrentClient, "_make_request", fake_make_request)

    torrents = await client.get_torrents(filter_active=True)

    # Only the active uploader should be returned
    assert len(torrents) == 1
    assert torrents[0].hash == "B" * 40
    # Ensure trackers was called only for the active one
    assert calls["trackers"] == ["B" * 40]
