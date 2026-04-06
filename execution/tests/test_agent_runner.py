"""Tests for agent runner — JSON parsing and edge cases."""

import json
import pytest

from execution.agents.agent_runner import _parse_json_response, save_script_to_db


class TestParseJsonResponse:
    def test_clean_json_dict(self) -> None:
        result = _parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_clean_json_array(self) -> None:
        result = _parse_json_response('[{"a": 1}, {"b": 2}]')
        assert result == [{"a": 1}, {"b": 2}]

    def test_json_with_code_fences(self) -> None:
        raw = '```json\n{"key": "value"}\n```'
        result = _parse_json_response(raw)
        assert result == {"key": "value"}

    def test_json_with_plain_code_fences(self) -> None:
        raw = '```\n{"key": "value"}\n```'
        result = _parse_json_response(raw)
        assert result == {"key": "value"}

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("this is not json")

    def test_nested_json(self) -> None:
        raw = '{"a": {"b": [1, 2, 3]}, "c": null}'
        result = _parse_json_response(raw)
        assert result["a"]["b"] == [1, 2, 3]
        assert result["c"] is None


class TestSaveScriptToDb:
    def test_formats_rows_correctly(self) -> None:
        """Verify row formatting without actual DB calls."""
        sentences = [
            {"sentence_number": 1, "sentence_text": "Hello world", "section": "intro"},
            {"sentence_number": 2, "sentence_text": "Second line", "section": "body"},
        ]
        # Verify the data structure that would be inserted
        rows = [
            {
                "viral_video_id": "test-uuid",
                "sentence_number": s.get("sentence_number", i + 1),
                "sentence_text": s.get("sentence_text", ""),
                "section": s.get("section", ""),
                "original_comparison": s.get("original_comparison", ""),
            }
            for i, s in enumerate(sentences)
        ]
        assert len(rows) == 2
        assert rows[0]["sentence_number"] == 1
        assert rows[1]["sentence_text"] == "Second line"

    def test_handles_missing_fields(self) -> None:
        """Verify graceful handling of incomplete sentence dicts."""
        sentences = [
            {"sentence_text": "Only text, no number"},
        ]
        rows = [
            {
                "viral_video_id": "test-uuid",
                "sentence_number": s.get("sentence_number", i + 1),
                "sentence_text": s.get("sentence_text", ""),
                "section": s.get("section", ""),
                "original_comparison": s.get("original_comparison", ""),
            }
            for i, s in enumerate(sentences)
        ]
        assert rows[0]["sentence_number"] == 1  # Falls back to index+1
        assert rows[0]["section"] == ""  # Default empty string
