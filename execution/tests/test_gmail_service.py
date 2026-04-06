"""Tests for Gmail approval service."""

import pytest

from execution.services.gmail_service import _parse_decision


class TestParseDecision:
    def test_yes_lowercase(self) -> None:
        assert _parse_decision("yes") is True

    def test_no_lowercase(self) -> None:
        assert _parse_decision("no") is False

    def test_y_shorthand(self) -> None:
        assert _parse_decision("y") is True

    def test_n_shorthand(self) -> None:
        assert _parse_decision("n") is False

    def test_approve(self) -> None:
        assert _parse_decision("approve") is True

    def test_reject(self) -> None:
        assert _parse_decision("reject") is False

    def test_skip(self) -> None:
        assert _parse_decision("skip") is False

    def test_yes_with_trailing_text(self) -> None:
        assert _parse_decision("yes please go ahead") is True

    def test_no_with_explanation(self) -> None:
        assert _parse_decision("no not this one") is False

    def test_case_insensitive(self) -> None:
        assert _parse_decision("YES") is True
        assert _parse_decision("No") is False

    def test_with_whitespace(self) -> None:
        assert _parse_decision("  yes  \n") is True

    def test_yes_with_quoted_reply(self) -> None:
        reply = "yes\n\nOn Mon, Jan 1 2026, bot wrote:\n> Reply YES to approve"
        assert _parse_decision(reply) is True

    def test_unrecognized(self) -> None:
        assert _parse_decision("maybe later") is None

    def test_empty_string(self) -> None:
        assert _parse_decision("") is None

    def test_random_text(self) -> None:
        assert _parse_decision("what is this about?") is None
