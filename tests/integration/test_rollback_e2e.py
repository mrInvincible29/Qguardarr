"""End-to-end rollback test with real Docker services.

Flow:
- Add 1-2 test torrents to qBittorrent (paused, via magnet).
- Set finite per-torrent upload limits (simulate managed state).
- Seed Qguardarr's rollback database inside the container with original limits
  (old_limit = -1 for unlimited) for those hashes.
- Call Qguardarr /rollback and verify qBittorrent shows unlimited (-1).
"""

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import List

import httpx
import pytest

from src.config import QBittorrentSettings
from src.qbit_client import QBittorrentClient


@pytest.mark.integration
@pytest.mark.docker
@pytest.mark.qbittorrent
@pytest.mark.qguardarr
async def test_rollback_end_to_end(docker_services):
    if not docker_services.get("qbittorrent"):
        pytest.skip("qBittorrent not available")
    if not docker_services.get("qguardarr"):
        pytest.skip("Qguardarr not available")

    # 1) Connect to qBittorrent
    qbit = QBittorrentClient(
        QBittorrentSettings(
            host="localhost",
            port=8080,
            username="admin",
            password="adminpass123",
            timeout=30,
        )
    )
    await qbit.connect()

    # 2) Add 1-2 test torrents via magnet (paused)
    # Prefer local test data if available
    test_data_path = Path("tests/test-data/real_torrents.json")
    torrents = []
    if test_data_path.exists():
        try:
            torrents = json.loads(test_data_path.read_text())
        except Exception:
            torrents = []

    # Fallback to a minimal entry if dataset missing
    if not torrents:
        torrents = [
            {
                "name": "Test Torrent",
                "magnet": "magnet:?xt=urn:btih:b2c3d4e5f6789a0b1c2d3e4f567890abcdef1234&dn=test",
                "size_mb": 10,
            }
        ]

    added_hashes: List[str] = []
    max_to_add = 2
    for t in torrents[:max_to_add]:
        ok = await qbit.add_torrent_from_magnet(
            t["magnet"], category="qguardarr-test", paused=True
        )
        assert ok is True
        await asyncio.sleep(1.5)

        # Find by category
        all_torrents = await qbit.get_torrents(filter_active=False)
        for ti in all_torrents:
            if ti.category == "qguardarr-test" and ti.hash not in added_hashes:
                added_hashes.append(ti.hash)
                break

    assert added_hashes, "No torrents were added to qBittorrent"

    # 3) Set finite per-torrent upload limits to simulate managed state
    finite_limit = 512000  # 0.5 MB/s
    for h in added_hashes:
        await qbit.set_torrent_upload_limit(h, finite_limit)
        await asyncio.sleep(0.2)
        current = await qbit.get_torrent_upload_limit(h)
        assert current > 0 and current != -1

    # 4) Seed Qguardarr's rollback DB inside the container for these hashes
    # Create a temporary SQLite DB file with required schema + entries
    temp_db = Path("./tests/test-data/rollback_seed_e2e.db")
    try:
        if temp_db.exists():
            temp_db.unlink()
        conn = sqlite3.connect(str(temp_db))
        cur = conn.cursor()
        cur.execute(
            (
                "CREATE TABLE IF NOT EXISTS rollback_entries (\n"
                " id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                " torrent_hash TEXT NOT NULL,\n"
                " old_limit INTEGER NOT NULL,\n"
                " new_limit INTEGER NOT NULL,\n"
                " tracker_id TEXT NOT NULL,\n"
                " timestamp INTEGER NOT NULL,\n"
                " reason TEXT DEFAULT '',\n"
                " restored INTEGER DEFAULT 0,\n"
                " created_at DATETIME DEFAULT CURRENT_TIMESTAMP\n"
                ")"
            )
        )

        ts = int(time.time())
        for h in added_hashes:
            cur.execute(
                "INSERT INTO rollback_entries (torrent_hash, old_limit, new_limit, tracker_id, timestamp, reason, restored) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (h, -1, finite_limit, "default", ts, "e2e-seed"),
            )
        conn.commit()
        conn.close()

        # Copy DB into the running container
        # Container name is set in docker-compose.test.yml as qguardarr-test
        cp_cmd = [
            "docker",
            "cp",
            str(temp_db),
            "qguardarr-test:/app/data/rollback.db",
        ]
        res = subprocess.run(cp_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"docker cp failed: {res.returncode}, {res.stderr}"
    finally:
        if temp_db.exists():
            try:
                temp_db.unlink()
            except Exception:
                pass

    # 5) Call Qguardarr /rollback endpoint
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8089/rollback",
            json={"confirm": True, "reason": "e2e"},
            timeout=30.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("changes_reversed", 0) >= len(added_hashes)

    # 6) Verify per-torrent limits are unlimited (-1)
    for h in added_hashes:
        lim = await qbit.get_torrent_upload_limit(h)
        assert lim == -1

    # Cleanup: remove torrents
    await qbit.delete_torrent("|".join(added_hashes), delete_files=False)
    await qbit.disconnect()
