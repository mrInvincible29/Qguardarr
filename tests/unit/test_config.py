"""Tests for configuration module"""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.config import ConfigLoader, QguardarrConfig, TrackerConfig


class TestConfigLoader:
    """Test configuration loading and validation"""

    def test_load_valid_config(self):
        """Test loading valid configuration"""
        config_data = {
            "global": {
                "update_interval": 300,
                "active_torrent_threshold_kb": 10,
                "max_api_calls_per_cycle": 500,
                "differential_threshold": 0.2,
                "rollout_percentage": 10,
                "host": "0.0.0.0",
                "port": 8089,
            },
            "qbittorrent": {
                "host": "localhost",
                "port": 8080,
                "username": "admin",
                "password": "test123",
                "timeout": 30,
            },
            "cross_seed": {
                "enabled": False,
                "url": None,
                "api_key": None,
                "timeout": 15,
            },
            "trackers": [
                {
                    "id": "test",
                    "name": "Test Tracker",
                    "pattern": ".*test\\.com.*",
                    "max_upload_speed": 5242880,
                    "priority": 5,
                },
                {
                    "id": "default",
                    "name": "Default",
                    "pattern": ".*",
                    "max_upload_speed": 2097152,
                    "priority": 1,
                },
            ],
            "rollback": {
                "database_path": "./data/rollback.db",
                "track_all_changes": True,
            },
            "logging": {
                "level": "INFO",
                "file": "./logs/qguardarr.log",
                "max_size_mb": 50,
                "backup_count": 5,
            },
        }

        # Create temporary config file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = Path(f.name)

        try:
            # Load config
            loader = ConfigLoader(config_path)
            config = loader.load_config()

            assert isinstance(config, QguardarrConfig)
            assert config.global_settings.update_interval == 300
            assert config.qbittorrent.host == "localhost"
            assert len(config.trackers) == 2
            assert config.trackers[0].id == "test"
            assert config.trackers[1].pattern == ".*"  # Catch-all must be last

        finally:
            config_path.unlink()

    def test_invalid_config_missing_catchall(self):
        """Test validation fails when catch-all pattern is missing"""
        config_data = {
            "global": {"update_interval": 300, "rollout_percentage": 10},
            "qbittorrent": {
                "host": "localhost",
                "port": 8080,
                "username": "admin",
                "password": "test",
            },
            "cross_seed": {"enabled": False},
            "trackers": [
                {
                    "id": "test",
                    "name": "Test",
                    "pattern": ".*test\\.com.*",
                    "max_upload_speed": 5242880,
                    "priority": 5,
                }
            ],
            "rollback": {
                "database_path": "./data/rollback.db",
                "track_all_changes": True,
            },
            "logging": {
                "level": "INFO",
                "file": "./logs/test.log",
                "max_size_mb": 50,
                "backup_count": 5,
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = Path(f.name)

        try:
            loader = ConfigLoader(config_path)
            with pytest.raises(ValueError, match="catch-all tracker"):
                loader.load_config()
        finally:
            config_path.unlink()

    def test_env_var_substitution(self):
        """Test environment variable substitution"""
        import os

        config_data = {
            "global": {"update_interval": 300, "rollout_percentage": 10},
            "qbittorrent": {
                "host": "localhost",
                "port": 8080,
                "username": "admin",
                "password": "${TEST_PASSWORD}",
            },
            "cross_seed": {"enabled": False},
            "trackers": [
                {
                    "id": "default",
                    "name": "Default",
                    "pattern": ".*",
                    "max_upload_speed": 2097152,
                    "priority": 1,
                }
            ],
            "rollback": {
                "database_path": "./data/rollback.db",
                "track_all_changes": True,
            },
            "logging": {
                "level": "INFO",
                "file": "./logs/test.log",
                "max_size_mb": 50,
                "backup_count": 5,
            },
        }

        # Set environment variable
        os.environ["TEST_PASSWORD"] = "secret123"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = Path(f.name)

        try:
            loader = ConfigLoader(config_path)
            config = loader.load_config()
            assert config.qbittorrent.password == "secret123"
        finally:
            config_path.unlink()
            del os.environ["TEST_PASSWORD"]


class TestTrackerConfig:
    """Test tracker configuration validation"""

    def test_valid_tracker_config(self):
        """Test valid tracker configuration"""
        config = TrackerConfig(
            id="test",
            name="Test Tracker",
            pattern=".*test\\.com.*",
            max_upload_speed=5242880,
            priority=5,
        )

        assert config.id == "test"
        assert config.priority == 5
        assert config.max_upload_speed == 5242880

    def test_invalid_regex_pattern(self):
        """Test invalid regex pattern validation"""
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            TrackerConfig(
                id="test",
                name="Test Tracker",
                pattern="[invalid regex",
                max_upload_speed=5242880,
                priority=5,
            )

    def test_invalid_priority_range(self):
        """Test priority validation"""
        with pytest.raises(ValueError):
            TrackerConfig(
                id="test",
                name="Test Tracker",
                pattern=".*test\\.com.*",
                max_upload_speed=5242880,
                priority=11,  # Outside valid range 1-10
            )


if __name__ == "__main__":
    pytest.main([__file__])
