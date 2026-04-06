"""Tests for comment scraper — row formatting and edge cases."""

import pytest

from execution.services.comment_scraper import _format_comment_rows


class TestFormatCommentRows:
    def test_single_thread_no_replies(self) -> None:
        threads = [{
            "snippet": {
                "topLevelComment": {
                    "id": "abc123",
                    "snippet": {
                        "authorDisplayName": "User1",
                        "authorChannelId": {"value": "UC123"},
                        "textOriginal": "Great video!",
                        "likeCount": 5,
                        "publishedAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-01-01T00:00:00Z",
                    },
                },
            },
        }]
        rows = _format_comment_rows(threads, "rec-uuid", "vid123")
        assert len(rows) == 1
        assert rows[0]["comment_id"] == "abc123"
        assert rows[0]["content"] == "Great video!"
        assert rows[0]["is_reply"] is False
        assert rows[0]["video_record_id"] == "rec-uuid"

    def test_thread_with_replies(self) -> None:
        threads = [{
            "snippet": {
                "topLevelComment": {
                    "id": "top1",
                    "snippet": {
                        "authorDisplayName": "Author",
                        "authorChannelId": {"value": "UC1"},
                        "textOriginal": "Top comment",
                        "likeCount": 10,
                        "publishedAt": "2026-01-01T00:00:00Z",
                    },
                },
            },
            "replies": {
                "comments": [
                    {
                        "id": "reply1",
                        "snippet": {
                            "authorDisplayName": "Replier",
                            "authorChannelId": {"value": "UC2"},
                            "textOriginal": "I agree",
                            "likeCount": 2,
                            "publishedAt": "2026-01-01T01:00:00Z",
                        },
                    },
                ],
            },
        }]
        rows = _format_comment_rows(threads, "rec-uuid", "vid123")
        assert len(rows) == 2
        assert rows[0]["is_reply"] is False
        assert rows[1]["is_reply"] is True
        assert rows[1]["parent_id"] == "top1"

    def test_empty_threads(self) -> None:
        rows = _format_comment_rows([], "rec-uuid", "vid123")
        assert rows == []

    def test_missing_author_channel_id(self) -> None:
        threads = [{
            "snippet": {
                "topLevelComment": {
                    "id": "c1",
                    "snippet": {
                        "authorDisplayName": "Anon",
                        "textOriginal": "Hello",
                        "likeCount": 0,
                    },
                },
            },
        }]
        rows = _format_comment_rows(threads, "rec-uuid", "vid123")
        assert rows[0]["author_channel_id"] == ""
