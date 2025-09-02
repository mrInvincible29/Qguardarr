"""Extra tests for ConfigLoader helpers."""

from src.config import ConfigLoader, QguardarrConfig


def test_config_loader_helpers(tmp_path):
    # Minimal config file
    cfg_text = """
global:
  update_interval: 300
  active_torrent_threshold_kb: 10
  max_api_calls_per_cycle: 500
  differential_threshold: 0.2
  rollout_percentage: 10
  host: "0.0.0.0"
  port: 8089
qbittorrent:
  host: "localhost"
  port: 8080
  username: "u"
  password: "p"
  timeout: 15
cross_seed:
  enabled: false
trackers:
  - id: default
    name: Default
    pattern: ".*"
    max_upload_speed: 1048576
    priority: 1
rollback:
  database_path: "./data/rollback.db"
  track_all_changes: true
logging:
  level: "INFO"
  file: "./logs/qguardarr.log"
  max_size_mb: 10
  backup_count: 3
"""
    p = tmp_path / "cfg.yaml"
    p.write_text(cfg_text)

    loader = ConfigLoader(p)
    cfg = loader.load_config()
    assert isinstance(cfg, QguardarrConfig)

    # Helpers
    assert loader.get_tracker_by_id("default").priority == 1
    pats = loader.get_tracker_patterns()
    assert "default" in pats and pats["default"].search("http://x/announce")

    # Format speed helper
    assert loader.format_speed(512) == "512 B/s"
    assert loader.format_speed(2048).endswith("KB/s")
    assert loader.format_speed(2 * 1024 * 1024).endswith("MB/s")
