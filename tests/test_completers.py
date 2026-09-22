# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest

# phabfive imports
from phabfive.cli import completers
from phabfive.cli.completers import (
    DEFAULT_PRIORITY_VALUES,
    DEFAULT_STATUS_VALUES,
    PATTERN_PREFIXES,
    complete_column,
    complete_column_change,
    complete_column_filter,
    complete_priority,
    complete_priority_change,
    complete_priority_filter,
    complete_status,
    complete_status_filter,
)


@pytest.fixture(autouse=True)
def no_api():
    """Use the default values instead of calling the API."""
    with patch.object(
        completers,
        "_get_values_with_api_fallback",
        side_effect=lambda fetch, default: default,
    ):
        yield


@pytest.fixture
def board_columns():
    """Fake the board lookup and record which board was asked for."""
    with patch.object(
        completers,
        "_get_board_columns",
        side_effect=lambda tag: ["Backlog", "Up Next", "Done"],
    ) as mock:
        yield mock


def _ctx(**params):
    ctx = MagicMock()
    ctx.params = params
    return ctx


def _has_pattern_syntax(completions):
    return any(":" in c or c in ("*", "raised", "lowered") for c in completions)


class TestPriorityCompletion:
    def test_create_offers_only_priority_names(self):
        assert complete_priority("") == DEFAULT_PRIORITY_VALUES

    def test_create_matches_prefix(self):
        assert complete_priority("h") == ["high"]

    def test_edit_adds_raise_lower(self):
        result = complete_priority_change("")
        assert result == DEFAULT_PRIORITY_VALUES + ["raise", "lower"]
        assert not _has_pattern_syntax(result)

    def test_edit_matches_prefix(self):
        assert complete_priority_change("l") == ["low", "lower"]

    def test_search_offers_patterns_and_keywords(self):
        result = complete_priority_filter("")
        assert "high" in result
        assert "in:high" in result
        assert "not:in:high" in result
        assert "raised" in result and "lowered" in result

    def test_search_completes_after_prefix(self):
        assert complete_priority_filter("from:h") == ["from:high"]

    def test_search_keyword_prefix(self):
        assert complete_priority_filter("r") == ["raised"]


class TestStatusCompletion:
    def test_create_and_edit_offer_only_status_names(self):
        result = complete_status("")
        assert result == DEFAULT_STATUS_VALUES
        assert not _has_pattern_syntax(result)

    def test_search_offers_patterns_and_keywords(self):
        result = complete_status_filter("")
        assert "open" in result
        assert "in:open" in result
        assert "raised" in result and "lowered" in result

    def test_search_completes_after_prefix(self):
        assert complete_status_filter("in:") == [
            f"in:{s}" for s in DEFAULT_STATUS_VALUES
        ]


class TestColumnCompletion:
    def test_create_offers_board_columns_only(self, board_columns):
        result = complete_column(_ctx(tag=["Sprint"]), [], "")
        assert result == ["Backlog", "Up Next", "Done"]

    def test_create_uses_first_repeated_tag_as_board(self, board_columns):
        """--tag is repeatable on create, so ctx.params holds a tuple."""
        complete_column(_ctx(tag=("Sprint", "Backend")), [], "")
        board_columns.assert_called_once_with("Sprint")

    def test_create_uses_first_comma_separated_tag_as_board(self, board_columns):
        """--tag=Board,Other names two tags, the first of them the board."""
        complete_column(_ctx(tag=["Board,Other"]), [], "")
        board_columns.assert_called_once_with("Board")

    def test_create_without_tag_offers_nothing(self, board_columns):
        assert complete_column(_ctx(tag=None), [], "") == []
        assert complete_column(_ctx(tag=()), [], "") == []
        board_columns.assert_not_called()

    def test_edit_adds_forward_backward(self, board_columns):
        result = complete_column_change(_ctx(tag="Sprint"), [], "")
        assert result == ["forward", "backward", "Backlog", "Up Next", "Done"]

    def test_edit_matches_case_insensitively(self, board_columns):
        assert complete_column_change(_ctx(tag="Sprint"), [], "up") == ["Up Next"]

    def test_search_offers_patterns_wildcard_and_columns(self, board_columns):
        result = complete_column_filter(_ctx(tag="Sprint"), [], "")
        assert result[:2] == ["forward", "backward"]
        for prefix in PATTERN_PREFIXES:
            assert prefix in result
        assert "*" in result
        assert result[-3:] == ["Backlog", "Up Next", "Done"]

    def test_search_without_tag_skips_columns(self, board_columns):
        result = complete_column_filter(_ctx(tag=None), [], "")
        assert "*" in result
        board_columns.assert_not_called()
