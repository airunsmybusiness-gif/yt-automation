"""Tests for YouTube API discovery service."""

import pytest
from datetime import datetime, timezone, timedelta

from execution.services.youtube_api import (
    _extract_video_id,
    _is_short,
    _parse_duration_seconds,
    is_viral,
)


# ---------------------------------------------------------------------------
# _extract_video_id
# ---------------------------------------------------------------------------

class TestExtractVideoId:
    def test_standard_url(self) -> None:
        assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self) -> None:
        assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_embed_url(self) -> None:
        assert _extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_shorts_url(self) -> None:
        assert _extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_raw_id(self) -> None:
        assert _extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_garbage_returns_none(self) -> None:
        assert _extract_video_id("not-a-url-or-id") is None

    def test_empty_string(self) -> None:
        assert _extract_video_id("") is None


# ---------------------------------------------------------------------------
# _parse_duration_seconds
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_minutes_and_seconds(self) -> None:
        assert _parse_duration_seconds("PT10M30S") == 630

    def test_hours(self) -> None:
        assert _parse_duration_seconds("PT1H2M3S") == 3723

    def test_seconds_only(self) -> None:
        assert _parse_duration_seconds("PT45S") == 45

    def test_empty(self) -> None:
        assert _parse_duration_seconds("") == 0

    def test_invalid(self) -> None:
        assert _parse_duration_seconds("garbage") == 0


# ---------------------------------------------------------------------------
# _is_short
# ---------------------------------------------------------------------------

class TestIsShort:
    def test_under_60s_is_short(self) -> None:
        video = {"contentDetails": {"duration": "PT30S"}, "snippet": {"title": "test", "description": ""}}
        assert _is_short(video) is True

    def test_10min_not_short(self) -> None:
        video = {"contentDetails": {"duration": "PT10M"}, "snippet": {"title": "test", "description": ""}}
        assert _is_short(video) is False

    def test_shorts_hashtag_under_3min(self) -> None:
        video = {"contentDetails": {"duration": "PT2M"}, "snippet": {"title": "Cool #shorts video", "description": ""}}
        assert _is_short(video) is True

    def test_shorts_hashtag_over_3min(self) -> None:
        video = {"contentDetails": {"duration": "PT5M"}, "snippet": {"title": "#shorts but long", "description": ""}}
        assert _is_short(video) is False


# ---------------------------------------------------------------------------
# is_viral
# ---------------------------------------------------------------------------

class TestIsViral:
    THRESHOLD = {"minViews": 7000, "earlyViews": 4000, "earlyHours": 12, "maxAgeHours": 48}

    def _make_video(self, views: int, hours_ago: float, duration: str = "PT10M") -> dict:
        published = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return {
            "statistics": {"viewCount": str(views)},
            "snippet": {"publishedAt": published.isoformat(), "title": "test", "description": ""},
            "contentDetails": {"duration": duration},
        }

    def test_viral_high_views(self) -> None:
        video = self._make_video(views=10000, hours_ago=24)
        assert is_viral(video, self.THRESHOLD) is True

    def test_viral_early_detection(self) -> None:
        video = self._make_video(views=5000, hours_ago=6)
        assert is_viral(video, self.THRESHOLD) is True

    def test_not_viral_low_views(self) -> None:
        video = self._make_video(views=500, hours_ago=24)
        assert is_viral(video, self.THRESHOLD) is False

    def test_not_viral_too_old(self) -> None:
        video = self._make_video(views=50000, hours_ago=72)
        assert is_viral(video, self.THRESHOLD) is False

    def test_early_but_not_enough_views(self) -> None:
        video = self._make_video(views=2000, hours_ago=6)
        assert is_viral(video, self.THRESHOLD) is False

    def test_missing_published_at(self) -> None:
        video = {"statistics": {"viewCount": "99999"}, "snippet": {}, "contentDetails": {"duration": "PT10M"}}
        assert is_viral(video, self.THRESHOLD) is False

    def test_short_excluded(self) -> None:
        video = self._make_video(views=50000, hours_ago=6, duration="PT30S")
        assert is_viral(video, self.THRESHOLD) is False

    def test_long_video_excluded(self) -> None:
        video = self._make_video(views=50000, hours_ago=6, duration="PT45M")
        assert is_viral(video, self.THRESHOLD) is False
