"""Tests for QBittorrentClient._authenticate backoff and ban paths."""

import pytest

from src.config import QBittorrentSettings
from src.qbit_client import QBittorrentClient


class FR:
    def __init__(self, status_code=200, text="Ok."):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    async def post(self, url, data=None):
        self.calls += 1
        item = self.seq.pop(0) if self.seq else FR(200, "Ok.")
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.asyncio
async def test_authenticate_progressive_delay(monkeypatch):
    # First attempt returns 200 with non-Ok text, then Ok
    client = QBittorrentClient(
        QBittorrentSettings(
            host="remote", port=8080, username="admin", password="pass", timeout=5
        )
    )
    client.session = FakeSession([FR(200, "Fail"), FR(200, "Ok.")])

    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("src.qbit_client.asyncio.sleep", fake_sleep)
    await client._authenticate()
    # Progressive delay called once with 1.5s after first failure
    assert slept and slept[0] == 1.5
    assert client.authenticated is True


@pytest.mark.asyncio
async def test_authenticate_ip_ban_backoff(monkeypatch):
    client = QBittorrentClient(
        QBittorrentSettings(
            host="remote", port=8080, username="admin", password="pass", timeout=5
        )
    )
    # First attempt raises 403-like error, then Ok
    client.session = FakeSession([Exception("403 Forbidden"), FR(200, "Ok.")])

    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr("src.qbit_client.asyncio.sleep", fake_sleep)
    await client._authenticate()
    # Ban backoff sleep should be 2.0 seconds on first attempt
    assert 2.0 in slept
    assert client.authenticated is True


@pytest.mark.asyncio
async def test_authenticate_all_fail(monkeypatch):
    client = QBittorrentClient(
        QBittorrentSettings(
            host="remote", port=8080, username="admin", password="pass", timeout=5
        )
    )

    # Session that always returns non-Ok response
    class AlwaysFailSession:
        async def post(self, url, data=None):
            return FR(200, "Nope")

    client.session = AlwaysFailSession()

    async def fake_sleep(d):
        return None

    monkeypatch.setattr("src.qbit_client.asyncio.sleep", fake_sleep)
    with pytest.raises(RuntimeError):
        await client._authenticate()
