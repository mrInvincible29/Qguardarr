import pytest
from fastapi.testclient import TestClient


def test_config_reload_applies_to_components(monkeypatch):
    import src.main as main
    from src.config import (
        CrossSeedSettings,
        GlobalSettings,
        LoggingSettings,
        QBittorrentSettings,
        QguardarrConfig,
        RollbackSettings,
        TrackerConfig,
    )

    # Build initial config
    cfg1 = QguardarrConfig(
        **{
            "global": GlobalSettings(update_interval=300, rollout_percentage=10),
            "qbittorrent": QBittorrentSettings(
                host="h1", port=8080, username="u", password="p"
            ),
            "cross_seed": CrossSeedSettings(enabled=False),
            "trackers": [
                TrackerConfig(
                    id="default",
                    name="Default",
                    pattern=".*",
                    max_upload_speed=123,
                    priority=1,
                )
            ],
            "rollback": RollbackSettings(database_path="./data/rb.db"),
            "logging": LoggingSettings(),
        }
    )

    # And new config to reload
    cfg2 = QguardarrConfig(
        **{
            "global": GlobalSettings(update_interval=60, rollout_percentage=55),
            "qbittorrent": QBittorrentSettings(
                host="h2", port=9090, username="uu", password="pp"
            ),
            "cross_seed": CrossSeedSettings(enabled=True, url="http://c"),
            "trackers": [
                TrackerConfig(
                    id="only_default",
                    name="Default",
                    pattern=".*",
                    max_upload_speed=999,
                    priority=1,
                )
            ],
            "rollback": RollbackSettings(database_path="./data/rb2.db"),
            "logging": LoggingSettings(),
        }
    )

    # Dummy loader that returns cfg2 on reload
    class DummyLoader:
        def reload_config(self):
            return cfg2

    # Dummy tracker matcher capturing updates
    class DummyMatcher:
        def __init__(self):
            self.updated = False
            self.last = None

        def update_tracker_configs(self, new):
            self.updated = True
            self.last = new

    # Dummy engine tracking rollout and dry-run
    class DummyEngine:
        def __init__(self):
            self.rollout = 10
            self.dry_run = False
            self.dry_run_store = None
            self.config = cfg1

        def update_rollout_percentage(self, p):
            self.rollout = p

    class DummyWebhook:
        def __init__(self, config):
            self.config = config
            self.cross_seed_forwarder = type("F", (), {"config": config})()

    # Inject state
    main.app_state["config_loader"] = DummyLoader()
    main.app_state["config"] = cfg1
    matcher = DummyMatcher()
    engine = DummyEngine()
    wh = DummyWebhook(cfg1)
    main.app_state["tracker_matcher"] = matcher
    main.app_state["allocation_engine"] = engine
    main.app_state["webhook_handler"] = wh

    client = TestClient(main.app)
    r = client.post("/config/reload")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reloaded"
    assert body["rollout_percentage"] == 55
    # Components updated
    assert matcher.updated and matcher.last[0].id == "only_default"
    assert engine.rollout == 55
    assert main.app_state["config"].qbittorrent.host == "h2"
    assert wh.config.qbittorrent.host == "h2"
