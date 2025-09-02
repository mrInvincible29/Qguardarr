"""Configuration management for Qguardarr"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class TrackerConfig(BaseModel):
    """Individual tracker configuration"""

    id: str
    name: str
    pattern: str
    max_upload_speed: int = Field(
        gt=0, description="Max upload speed in bytes/sec"
    )
    priority: int = Field(ge=1, le=10, default=1, description="Priority 1-10")

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v) -> str:
        """Ensure the regex pattern is valid"""
        try:
            re.compile(v)
            return v
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")


class GlobalSettings(BaseModel):
    """Global application settings"""

    update_interval: int = Field(
        default=300, ge=60, description="Update interval in seconds"
    )
    active_torrent_threshold_kb: int = Field(
        default=10, ge=1, description="Threshold for active torrents in KB/s"
    )
    max_api_calls_per_cycle: int = Field(default=500, ge=100)
    differential_threshold: float = Field(default=0.2, ge=0.05, le=1.0)
    rollout_percentage: int = Field(default=10, ge=1, le=100)
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8089, ge=1024, le=65535)


class QBittorrentSettings(BaseModel):
    """qBittorrent connection settings"""

    host: str = Field(default="localhost")
    port: int = Field(default=8080, ge=1024, le=65535)
    username: str
    password: str
    timeout: int = Field(default=30, ge=5, le=120)


class CrossSeedSettings(BaseModel):
    """Cross-seed integration settings"""

    enabled: bool = Field(default=False)
    url: Optional[str] = None
    api_key: Optional[str] = None
    timeout: int = Field(default=15, ge=5, le=60)


class RollbackSettings(BaseModel):
    """Rollback system settings"""

    database_path: str = Field(default="./data/rollback.db")
    track_all_changes: bool = Field(default=True)


class LoggingSettings(BaseModel):
    """Logging configuration"""

    level: str = Field(default="INFO")
    file: str = Field(default="./logs/qguardarr.log")
    max_size_mb: int = Field(default=50, ge=1)
    backup_count: int = Field(default=5, ge=1)


class QguardarrConfig(BaseModel):
    """Main configuration model"""

    global_settings: GlobalSettings = Field(alias="global")
    qbittorrent: QBittorrentSettings
    cross_seed: CrossSeedSettings
    trackers: List[TrackerConfig]
    rollback: RollbackSettings
    logging: LoggingSettings

    @field_validator("trackers")
    @classmethod
    def validate_trackers(cls, v) -> List[TrackerConfig]:
        """Ensure trackers configuration is valid"""
        if not v:
            raise ValueError("At least one tracker must be configured")

        # Check for duplicate IDs
        ids = [tracker.id for tracker in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate tracker IDs found")

        # Ensure catch-all pattern exists and is last
        catch_all_found = False
        for i, tracker in enumerate(v):
            if tracker.pattern == ".*":
                if i != len(v) - 1:
                    raise ValueError(
                        "Catch-all pattern (.*) must be the last tracker"
                    )
                catch_all_found = True

        if not catch_all_found:
            raise ValueError(
                "A catch-all tracker with pattern '.*' must be "
                "configured as the last tracker"
            )

        return v


class ConfigLoader:
    """Configuration loader with environment variable substitution"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path("config/qguardarr.yaml")
        self._config: Optional[QguardarrConfig] = None

    def _substitute_env_vars(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively substitute environment variables in config data"""
        if isinstance(data, dict):
            return {
                key: self._substitute_env_vars(value)
                for key, value in data.items()
            }
        elif isinstance(data, list):
            return [self._substitute_env_vars(item) for item in data]
        elif isinstance(data, str):
            # Replace ${VAR_NAME} with environment variable value
            pattern = re.compile(r"\$\{([^}]+)\}")

            def replace_var(match):
                var_name = match.group(1)
                # Keep original if not found
                return os.getenv(var_name, match.group(0))

            return pattern.sub(replace_var, data)
        else:
            return data

    def load_config(self) -> QguardarrConfig:
        """Load and validate configuration from YAML file"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}"
            )

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)

            # Substitute environment variables
            processed_config = self._substitute_env_vars(raw_config)

            # Validate configuration
            self._config = QguardarrConfig(**processed_config)
            return self._config

        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML configuration: {e}")
        except Exception as e:
            raise ValueError(f"Configuration validation failed: {e}")

    def reload_config(self) -> QguardarrConfig:
        """Reload configuration (for hot reload)"""
        return self.load_config()

    @property
    def config(self) -> Optional[QguardarrConfig]:
        """Get current configuration"""
        return self._config

    def get_tracker_by_id(self, tracker_id: str) -> Optional[TrackerConfig]:
        """Get tracker configuration by ID"""
        if not self._config:
            return None

        for tracker in self._config.trackers:
            if tracker.id == tracker_id:
                return tracker
        return None

    def get_tracker_patterns(self) -> Dict[str, re.Pattern]:
        """Get compiled regex patterns for all trackers"""
        if not self._config:
            return {}

        patterns = {}
        for tracker in self._config.trackers:
            try:
                patterns[tracker.id] = re.compile(tracker.pattern, re.IGNORECASE)
            except re.error:
                # Skip invalid patterns (should be caught in validation)
                continue

        return patterns

    def format_speed(self, speed_bytes: int) -> str:
        """Format speed in bytes to human readable format"""
        if speed_bytes < 1024:
            return f"{speed_bytes} B/s"
        elif speed_bytes < 1048576:
            return f"{speed_bytes / 1024:.1f} KB/s"
        elif speed_bytes < 1073741824:
            return f"{speed_bytes / 1048576:.1f} MB/s"
        else:
            return f"{speed_bytes / 1073741824:.1f} GB/s"
