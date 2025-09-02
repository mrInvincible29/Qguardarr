"""Tests for tracker matcher"""

import pytest

from src.config import TrackerConfig
from src.tracker_matcher import TrackerMatcher


class TestTrackerMatcher:
    """Test tracker pattern matching"""

    @pytest.fixture
    def tracker_configs(self):
        """Sample tracker configurations"""
        return [
            TrackerConfig(
                id="private1",
                name="Private Tracker 1",
                pattern=".*private1\\.net.*",
                max_upload_speed=10485760,  # 10 MB/s
                priority=10,
            ),
            TrackerConfig(
                id="public1",
                name="Public Tracker 1",
                pattern=".*public1\\.org.*",
                max_upload_speed=5242880,  # 5 MB/s
                priority=5,
            ),
            TrackerConfig(
                id="default",
                name="Default",
                pattern=".*",  # Catch-all
                max_upload_speed=2097152,  # 2 MB/s
                priority=1,
            ),
        ]

    @pytest.fixture
    def matcher(self, tracker_configs):
        """Tracker matcher instance"""
        return TrackerMatcher(tracker_configs)

    def test_specific_pattern_match(self, matcher):
        """Test matching specific tracker patterns"""
        # Should match private1
        result = matcher.match_tracker("http://private1.net/announce")
        assert result == "private1"

        # Should match public1
        result = matcher.match_tracker("https://public1.org/tracker/announce")
        assert result == "public1"

    def test_catch_all_pattern(self, matcher):
        """Test catch-all pattern for unknown trackers"""
        result = matcher.match_tracker("http://unknown-tracker.com/announce")
        assert result == "default"

        result = matcher.match_tracker("https://some-random-site.info/tracker")
        assert result == "default"

    def test_empty_tracker_url(self, matcher):
        """Test empty tracker URL handling"""
        result = matcher.match_tracker("")
        assert result == "default"

        result = matcher.match_tracker(None)
        assert result == "default"

    def test_case_insensitive_matching(self, matcher):
        """Test case insensitive pattern matching"""
        result = matcher.match_tracker("HTTP://PRIVATE1.NET/ANNOUNCE")
        assert result == "private1"

        result = matcher.match_tracker("https://PUBLIC1.ORG/tracker")
        assert result == "public1"

    def test_cache_functionality(self, matcher):
        """Test URL caching functionality"""
        url = "http://private1.net/announce"

        # First call should be cache miss
        result1 = matcher.match_tracker(url)
        assert result1 == "private1"
        assert matcher.stats["cache_misses"] == 1
        assert matcher.stats["cache_hits"] == 0

        # Second call should be cache hit
        result2 = matcher.match_tracker(url)
        assert result2 == "private1"
        assert matcher.stats["cache_hits"] == 1

    def test_bulk_matching(self, matcher):
        """Test bulk tracker matching"""
        urls = [
            "http://private1.net/announce",
            "https://public1.org/tracker",
            "http://unknown.com/announce",
        ]

        results = matcher.bulk_match_trackers(urls)

        expected = {
            "http://private1.net/announce": "private1",
            "https://public1.org/tracker": "public1",
            "http://unknown.com/announce": "default",
        }

        assert results == expected

    def test_get_tracker_config(self, matcher):
        """Test getting tracker configuration by ID"""
        config = matcher.get_tracker_config("private1")
        assert config is not None
        assert config.id == "private1"
        assert config.max_upload_speed == 10485760

        # Non-existent tracker
        config = matcher.get_tracker_config("nonexistent")
        assert config is None

    def test_get_tracker_limits(self, matcher):
        """Test getting all tracker limits"""
        limits = matcher.get_tracker_limits()

        expected = {
            "private1": 10485760,
            "public1": 5242880,
            "default": 2097152,
        }

        assert limits == expected

    def test_group_and_priorities_and_update(self, matcher, tracker_configs):
        """Test grouping torrents, priorities, and config hot-reload"""
        torrents = [
            {"hash": "h1", "tracker": "http://private1.net/announce"},
            {"hash": "h2", "tracker": "http://unknown.com/announce"},
        ]
        grouped = matcher.group_torrents_by_tracker(torrents)
        assert set(grouped.keys()) == {"private1", "default"}
        assert [t["hash"] for t in grouped["private1"]] == ["h1"]

        priorities = matcher.get_tracker_priorities()
        assert priorities["private1"] == 10
        assert priorities["default"] == 1

        # Hot reload configs
        new_configs = [
            TrackerConfig(
                id="only_default",
                name="Default",
                pattern=".*",
                max_upload_speed=123,
                priority=1,
            )
        ]
        matcher.update_tracker_configs(new_configs)
        assert matcher.get_tracker_config("only_default").max_upload_speed == 123

    def test_pattern_validation(self, matcher):
        """Test pattern validation"""
        errors = matcher.validate_patterns()
        assert len(errors) == 0  # Should be no errors

        # Test with missing catch-all pattern (valid regex but missing
        # catch-all)
        incomplete_configs = [
            TrackerConfig(
                id="test",
                name="Test",
                pattern=".*test\\.com.*",  # Valid regex
                max_upload_speed=1000000,
                priority=5,
            )
        ]

        incomplete_matcher = TrackerMatcher(incomplete_configs)
        errors = incomplete_matcher.validate_patterns()
        assert len(errors) > 0
        assert "catch-all pattern" in errors[0]  # Missing catch-all

    def test_cache_stats(self, matcher):
        """Test cache statistics"""
        # Make some requests
        matcher.match_tracker("http://private1.net/announce")
        matcher.match_tracker("http://private1.net/announce")  # Cache hit
        matcher.match_tracker("https://public1.org/tracker")

        stats = matcher.get_cache_stats()
        assert stats["cache_hits"] >= 1
        assert stats["cache_misses"] >= 2
        assert stats["cache_size"] >= 2
        assert 0 <= stats["hit_rate_percent"] <= 100

    def test_test_pattern_match(self, matcher):
        """Test pattern matching debugging"""
        result = matcher.test_pattern_match(
            "http://private1.net/announce", detailed=True
        )

        assert result["url"] == "http://private1.net/announce"
        assert result["matched_tracker"] == "private1"
        assert len(result["matches"]) == 3  # Should test all patterns

        # Check that private1 pattern matches
        private1_match = next(
            m for m in result["matches"] if m["tracker_id"] == "private1"
        )
        assert private1_match["matches"] is True


if __name__ == "__main__":
    pytest.main([__file__])
