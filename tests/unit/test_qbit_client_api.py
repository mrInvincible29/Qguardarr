"""Unit tests for QBittorrentClient API helpers (mocked _make_request)."""

import asyncio
from types import SimpleNamespace

from src.config import QBittorrentSettings
from src.qbit_client import QBittorrentClient


def make_client() -> QBittorrentClient:
    return QBittorrentClient(
        QBittorrentSettings(host="localhost", port=8080, username="u", password="p")
    )


class FakeResponse:
    def __init__(self, json_data=None, text=""):
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


async def test_get_version_with_and_without_build_info():
    c = make_client()

    calls = {"count": 0}

    async def fake_make_request(method, endpoint, **kwargs):
        calls["count"] += 1
        if endpoint == "/api/v2/app/version":
            return FakeResponse(text='"4.6.0"')
        if endpoint == "/api/v2/app/buildInfo":
            return FakeResponse(json_data={"bitness": 64, "qt": "5.15"})
        raise AssertionError("unexpected endpoint")

    c._make_request = fake_make_request  # type: ignore[attr-defined]

    v = await c.get_version()
    assert v["version"] == "4.6.0"
    assert v["build_info"]["bitness"] == 64

    # Now simulate buildInfo failure
    async def fake_make_request_fail(method, endpoint, **kwargs):
        if endpoint == "/api/v2/app/version":
            return FakeResponse(text='"4.6.1"')
        if endpoint == "/api/v2/app/buildInfo":
            raise RuntimeError("no build info")
        raise AssertionError("unexpected endpoint")

    c._make_request = fake_make_request_fail  # type: ignore[attr-defined]
    v2 = await c.get_version()
    assert v2 == {"version": "4.6.1"}


async def test_get_preferences_and_trackers_and_props():
    c = make_client()

    async def fake_make_request(method, endpoint, **kwargs):
        if endpoint == "/api/v2/app/preferences":
            return FakeResponse(json_data={"save_path": "/downloads"})
        if endpoint == "/api/v2/torrents/trackers":
            return FakeResponse(
                json_data=[
                    {
                        "url": "http://t/announce",
                        "status": 2,
                        "tier": 0,
                        "num_peers": 10,
                        "num_seeds": 8,
                        "num_leeches": 2,
                    },
                    {"url": "** error **", "status": 1},
                ]
            )
        if endpoint == "/api/v2/torrents/properties":
            return FakeResponse(json_data={"up_limit": 123456})
        raise AssertionError("unexpected endpoint")

    c._make_request = fake_make_request  # type: ignore[attr-defined]

    prefs = await c.get_preferences()
    assert prefs["save_path"] == "/downloads"

    trackers = await c.get_torrent_trackers("deadbeef")
    assert trackers[0]["url"].startswith("http")
    assert trackers[0]["status"] == 2

    up = await c.get_torrent_upload_limit("deadbeef")
    assert up == 123456
