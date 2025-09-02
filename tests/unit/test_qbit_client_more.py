"""Additional unit tests for QBittorrentClient behaviors (no real network)"""

import asyncio
from typing import Any, Dict, List

import pytest

from src.qbit_client import QBittorrentClient
from src.config import QBittorrentSettings


def mk_client() -> QBittorrentClient:
    return QBittorrentClient(
        QBittorrentSettings(host="localhost", port=8080, username="u", password="p", timeout=10)
    )


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", raise_http=False):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self._raise = raise_http

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self._raise:
            raise RuntimeError("HTTP error")
        return None


@pytest.mark.asyncio
async def test_make_request_reauth_on_403(monkeypatch):
    client = mk_client()

    # Stub authenticate to flip a flag
    called = {"auth": 0}

    async def fake_auth(self):
        called["auth"] += 1
        self.authenticated = True

    # Fake session returning 403 first, then 200
    class FakeSession:
        def __init__(self):
            self.calls = 0

        async def request(self, method, url, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(status_code=403, text="Forbidden")
            return FakeResponse(status_code=200, json_data={"ok": True})

    client.session = FakeSession()
    client.authenticated = True
    monkeypatch.setattr(QBittorrentClient, "_authenticate", fake_auth, raising=True)

    resp = await client._make_request("GET", "/api/v2/transfer/info")
    assert isinstance(resp, FakeResponse)
    assert client.session.calls == 2
    assert called["auth"] == 1


@pytest.mark.asyncio
async def test_make_request_circuit_open(monkeypatch):
    client = mk_client()
    client.circuit_breaker.state = "open"
    client.circuit_breaker.last_failure_time = 10**12  # far future
    client.circuit_breaker.recovery_timeout = 10**12

    with pytest.raises(RuntimeError, match="Circuit breaker is open"):
        await client._make_request("GET", "/x")


@pytest.mark.asyncio
async def test_get_version_with_build_info(monkeypatch):
    client = mk_client()

    async def fake_req(method, endpoint, **kwargs):
        if endpoint == "/api/v2/app/version":
            return FakeResponse(status_code=200, text='"4.6.0"')
        if endpoint == "/api/v2/app/buildInfo":
            return FakeResponse(status_code=200, json_data={"qt": "6", "libtorrent": "2"})
        raise AssertionError("Unexpected endpoint")

    monkeypatch.setattr(client, "_make_request", fake_req)
    v = await client.get_version()
    assert v["version"] == "4.6.0"
    assert v["build_info"]["qt"] == "6"


@pytest.mark.asyncio
async def test_get_preferences_and_stats(monkeypatch):
    client = mk_client()

    async def fake_req(method, endpoint, **kwargs):
        if endpoint == "/api/v2/app/preferences":
            return FakeResponse(json_data={"dht": True, "pex": True})
        if endpoint == "/api/v2/transfer/info":
            return FakeResponse(json_data={"dl_info_speed": 0})
        raise AssertionError("Unexpected endpoint")

    monkeypatch.setattr(client, "_make_request", fake_req)
    prefs = await client.get_preferences()
    stats = await client.get_global_stats()
    assert prefs["dht"] is True
    assert stats["dl_info_speed"] == 0


@pytest.mark.asyncio
async def test_get_torrents_and_trackers(monkeypatch):
    client = mk_client()

    torrents_list = [
        {"hash": "h1", "name": "t1", "state": "st", "progress": 1.0, "dlspeed": 0, "upspeed": 0, "priority": 0, "num_seeds": 1, "num_leechs": 1, "ratio": 1.0, "size": 1, "completed": 1},
        {"hash": "h2", "name": "t2", "state": "st", "progress": 1.0, "dlspeed": 0, "upspeed": 0, "priority": 0, "num_seeds": 1, "num_leechs": 1, "ratio": 1.0, "size": 1, "completed": 1},
    ]

    async def fake_req(method, endpoint, **kwargs):
        if endpoint == "/api/v2/torrents/info":
            return FakeResponse(json_data=torrents_list)
        if endpoint == "/api/v2/torrents/trackers":
            # First returns working tracker, second returns DHT then fallback
            h = kwargs.get("params", {}).get("hash")
            if h == "h1":
                return FakeResponse(json_data=[{"url": "http://tracker/announce", "status": 2}])
            return FakeResponse(json_data=[{"url": "** [DHT] **", "status": 1}, {"url": "udp://u", "status": 1}])
        raise AssertionError("Unexpected endpoint")

    monkeypatch.setattr(client, "_make_request", fake_req)
    ts = await client.get_torrents(filter_active=False)
    assert len(ts) == 2
    urls = {t.tracker for t in ts}
    assert "http://tracker/announce" in urls
    assert "udp://u" in urls


@pytest.mark.asyncio
async def test_upload_limit_helpers(monkeypatch):
    client = mk_client()
    recorded: List[Dict[str, Any]] = []

    async def fake_req(method, endpoint, **kwargs):
        recorded.append({"endpoint": endpoint, "data": kwargs.get("data"), "params": kwargs.get("params")})
        if endpoint == "/api/v2/torrents/properties":
            return FakeResponse(json_data={"up_limit": 1024})
        return FakeResponse()

    monkeypatch.setattr(client, "_make_request", fake_req)

    await client.set_torrent_upload_limit("hx", 2048)
    lim = await client.get_torrent_upload_limit("hx")
    assert lim == 1024

    await client.remove_torrent_upload_limits(["a", "b"], batch_size=10)
    # The grouping call goes through setUploadLimit with -1 limit
    assert any(rec for rec in recorded if rec["endpoint"] == "/api/v2/torrents/setUploadLimit")


@pytest.mark.asyncio
async def test_add_and_delete_torrent(monkeypatch):
    client = mk_client()

    async def good_req(method, endpoint, **kwargs):
        return FakeResponse()

    async def bad_req(method, endpoint, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(client, "_make_request", good_req)
    ok = await client.add_torrent_from_magnet("magnet:?xt=urn:btih:abc", category="cat", paused=True)
    assert ok is True
    await client.delete_torrent("deadbeef", delete_files=False)

    # Failure case
    monkeypatch.setattr(client, "_make_request", bad_req)
    ok2 = await client.add_torrent_from_magnet("magnet:?xt=urn:btih:abc")
    assert ok2 is False


@pytest.mark.asyncio
async def test_connect_sets_flags(monkeypatch):
    client = mk_client()

    async def fake_auth(self):
        self.authenticated = True

    monkeypatch.setattr(QBittorrentClient, "_authenticate", fake_auth, raising=True)
    await client.connect()
    st = client.get_stats()
    assert st["connected"] is True
    assert st["auth_time"] is not None

