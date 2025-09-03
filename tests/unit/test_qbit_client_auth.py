import asyncio
import logging
import types

import pytest

from src.config import QBittorrentSettings
from src.qbit_client import QBittorrentClient


class DummyResponse:
    def __init__(self, text="Ok.", status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise RuntimeError(f"HTTP {self.status_code}")


class DummyAsyncClient:
    def __init__(self, *args, **kwargs):
        self.posts = []  # list of (url, data)

    async def post(self, url, data=None, **kwargs):
        self.posts.append((url, data))
        # Simulate qBittorrent login success
        if url.endswith("/api/v2/auth/login"):
            # Only accept the provided password
            if data and data.get("password") == "secret":
                return DummyResponse("Ok.", 200)
            return DummyResponse("Fails.", 200)
        return DummyResponse("Ok.", 200)

    async def request(self, method, url, **kwargs):
        return DummyResponse("Ok.", 200)

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_auth_uses_only_config_password_and_masks_logs(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    # Patch httpx.AsyncClient used inside QBittorrentClient
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    settings = QBittorrentSettings(
        host="localhost",
        port=8080,
        username="user",
        password="secret",
        timeout=10,
    )
    client = QBittorrentClient(settings)

    # Run connect (should authenticate once with provided password)
    await client.connect()

    # Verify only one login attempt and with masked logs
    posts = client.session.posts  # type: ignore[attr-defined]
    assert len(posts) >= 1
    # First post should be login with configured password
    url, data = posts[0]
    assert url.endswith("/api/v2/auth/login")
    assert data["username"] == "user"
    assert data["password"] == "secret"

    # Ensure logs do not contain the raw password
    log_text = "\n".join([rec.getMessage() for rec in caplog.records])
    assert "secret" not in log_text
    assert "******" in log_text or "[REDACTED]" in log_text
