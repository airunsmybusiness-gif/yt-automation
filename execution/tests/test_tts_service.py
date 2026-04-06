"""Tests for TTS service — sentence grouping and JSONL building."""

import json
import pytest

from execution.services.tts_service import _build_tts_jsonl, _group_sentences


class TestGroupSentences:
    def test_exact_chunks(self) -> None:
        sentences = [
            {"sentence_number": i, "sentence_text": f"Sentence {i}"}
            for i in range(1, 11)
        ]
        chunks = _group_sentences(sentences, chunk_size=5)
        assert len(chunks) == 2
        assert chunks[0]["start_sentence_number"] == 1
        assert chunks[0]["end_sentence_number"] == 5
        assert chunks[1]["start_sentence_number"] == 6
        assert chunks[1]["end_sentence_number"] == 10

    def test_remainder_chunk(self) -> None:
        sentences = [
            {"sentence_number": i, "sentence_text": f"S{i}"}
            for i in range(1, 8)
        ]
        chunks = _group_sentences(sentences, chunk_size=5)
        assert len(chunks) == 2
        assert chunks[1]["sentence_count"] == 2

    def test_single_sentence(self) -> None:
        sentences = [{"sentence_number": 1, "sentence_text": "Only one"}]
        chunks = _group_sentences(sentences, chunk_size=5)
        assert len(chunks) == 1
        assert chunks[0]["combined_text"] == "Only one"

    def test_empty_input(self) -> None:
        chunks = _group_sentences([], chunk_size=5)
        assert chunks == []

    def test_combined_text_joins(self) -> None:
        sentences = [
            {"sentence_number": 1, "sentence_text": "Hello"},
            {"sentence_number": 2, "sentence_text": "world"},
        ]
        chunks = _group_sentences(sentences, chunk_size=5)
        assert chunks[0]["combined_text"] == "Hello world"


class TestBuildTtsJsonl:
    def test_produces_valid_jsonl(self) -> None:
        chunks = [
            {
                "start_sentence_number": 1,
                "end_sentence_number": 3,
                "combined_text": "Hello world test",
                "sentence_count": 3,
            },
        ]
        jsonl = _build_tts_jsonl(chunks)
        lines = jsonl.strip().split("\n")
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["key"] == "1"
        assert "contents" in parsed["request"]
        assert parsed["request"]["generation_config"]["response_modalities"] == ["AUDIO"]

    def test_multiple_chunks(self) -> None:
        chunks = [
            {"start_sentence_number": i, "end_sentence_number": i + 4,
             "combined_text": f"Chunk {i}", "sentence_count": 5}
            for i in range(1, 16, 5)
        ]
        jsonl = _build_tts_jsonl(chunks)
        lines = jsonl.strip().split("\n")
        assert len(lines) == 3

        keys = [json.loads(line)["key"] for line in lines]
        assert keys == ["1", "6", "11"]
