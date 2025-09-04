"""Integration test for automatic config hot-reload (file watcher)."""

import asyncio
import time
from pathlib import Path

import httpx
import pytest


@pytest.mark.docker
@pytest.mark.qguardarr
@pytest.mark.asyncio
async def test_config_hot_reload_automatic(docker_services):
    # Ensure Qguardarr is up
    if not docker_services.get("qguardarr"):
        pytest.skip("Qguardarr not available")

    base = "http://localhost:8089"

    async with httpx.AsyncClient() as client:
        # Read current config via API
        r = await client.get(f"{base}/config", timeout=5.0)
        assert r.status_code == 200
        cfg = r.json()
        before_rollout = int(cfg["global"]["rollout_percentage"]) if "global" in cfg else int(cfg["global_settings"]["rollout_percentage"])  # type: ignore[index]

    # Edit host-side config file that is bind-mounted read-only into the container
    cfg_path = Path("config/qguardarr.yaml")
    assert cfg_path.exists(), "config/qguardarr.yaml must exist for integration tests"
    text = cfg_path.read_text()

    # Bump rollout to a new value to detect
    new_rollout = before_rollout + 7 if before_rollout <= 90 else 42
    replaced = False
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("rollout_percentage:"):
            indent = line[: len(line) - len(line.lstrip(" "))]
            line = f"{indent}rollout_percentage: {new_rollout}"
            replaced = True
        lines.append(line)
    if not replaced:
        # Insert under 'global:' if missing for any reason
        new_lines = []
        inserted = False
        for line in lines:
            new_lines.append(line)
            if line.strip() == "global:":
                new_lines.append("  rollout_percentage: %d" % new_rollout)
                inserted = True
        lines = (
            new_lines
            if inserted
            else lines + ["global:", f"  rollout_percentage: {new_rollout}"]
        )

    cfg_path.write_text("\n".join(lines) + "\n")

    # Poll API until the watcher applies new config
    deadline = time.time() + 25
    seen = None
    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            r = await client.get(f"{base}/config", timeout=5.0)
            if r.status_code == 200:
                cfg = r.json()
                val = int(cfg["global"]["rollout_percentage"]) if "global" in cfg else int(cfg["global_settings"]["rollout_percentage"])  # type: ignore[index]
                seen = val
                if val == new_rollout:
                    break
            await asyncio.sleep(1.0)

    assert seen == new_rollout, f"Expected rollout {new_rollout}, saw {seen}"
