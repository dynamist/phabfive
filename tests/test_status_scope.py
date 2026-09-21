# -*- coding: utf-8 -*-
"""Tests for the open/closed/any status scope keywords (#419)."""

import pytest

from phabfive.transitions import parse_status_patterns
from phabfive.transitions.status import (
    StatusPattern,
    closed_status_names,
    resolve_status_scope,
    unreachable_conditions,
)

# What maniphest.querystatuses answers on a stock instance, plus a custom
# closed status whose name differs from its key by more than case
API = {
    "openStatuses": ["open", "blocked"],
    "closedStatuses": {"1": "resolved", "2": "wontfix", "3": "done-done"},
    "statusMap": {
        "open": "Open",
        "blocked": "Blocked",
        "resolved": "Resolved",
        "wontfix": "Wontfix",
        "done-done": "Shipped",
    },
}


def _scope(value, default="open"):
    return resolve_status_scope(parse_status_patterns(value, API), default)


def _types(patterns):
    return [[c["type"] for c in p.conditions] for p in patterns]


class TestParsing:
    @pytest.mark.parametrize("keyword", ["open", "closed", "any"])
    def test_keyword_parses_as_a_condition(self, keyword):
        (pattern,) = parse_status_patterns(keyword)

        assert pattern.conditions == [{"type": keyword}]
        assert str(pattern) == keyword

    def test_keyword_ands_with_a_pattern(self):
        (pattern,) = parse_status_patterns("closed+in:Resolved")

        assert str(pattern) == "closed+in:Resolved"

    def test_negated_keyword(self):
        (pattern,) = parse_status_patterns("not:open")

        assert pattern.conditions == [{"type": "open", "negated": True}]


class TestClosedStatusNames:
    def test_keys_and_names_from_the_api(self):
        assert closed_status_names(API) == {
            "resolved",
            "wontfix",
            "done-done",
            "shipped",
        }

    def test_fallback_without_the_api(self):
        assert "resolved" in closed_status_names(None)
        assert "open" not in closed_status_names(None)


class TestMatching:
    @pytest.mark.parametrize(
        "keyword, status, expected",
        [
            ("open", "Open", True),
            ("open", "Blocked", True),
            ("open", "Resolved", False),
            ("closed", "Resolved", True),
            ("closed", "Shipped", True),
            ("closed", "Open", False),
            ("any", "Open", True),
            ("any", "Shipped", True),
        ],
    )
    def test_keyword_matches_on_the_current_status(self, keyword, status, expected):
        pattern = StatusPattern([{"type": keyword}], API)

        assert pattern.matches([], status) is expected

    def test_negated_open_is_closed(self):
        pattern = StatusPattern([{"type": "open", "negated": True}], API)

        assert pattern.matches([], "Resolved") is True
        assert pattern.matches([], "Open") is False


class TestResolveStatusScope:
    def test_no_patterns_is_the_default(self):
        assert resolve_status_scope(None) == ("open", None)
        assert resolve_status_scope(None, "any") == ("any", None)

    @pytest.mark.parametrize("keyword", ["open", "closed", "any"])
    def test_a_keyword_alone_goes_to_the_server_and_leaves_no_pattern(self, keyword):
        assert _scope(keyword) == (keyword, None)

    @pytest.mark.parametrize(
        "value, scope",
        [
            ("open,closed", "any"),
            ("not:open", "closed"),
            ("not:closed", "open"),
            ("closed,any", "any"),
        ],
    )
    def test_keyword_only_groups_combine(self, value, scope):
        assert _scope(value) == (scope, None)

    def test_a_pattern_alone_keeps_the_open_default(self):
        scope, patterns = _scope("in:Resolved")

        # No history is fetched for closed tasks: the server scope is open
        assert scope == "open"
        assert _types(patterns) == [["in"]]

    def test_a_pattern_under_the_deprecated_all_reaches_every_status(self):
        scope, patterns = _scope("in:Resolved", default="any")

        assert scope == "any"
        assert _types(patterns) == [["in"]]

    def test_a_scoped_pattern_narrows_the_server(self):
        scope, patterns = _scope("closed+in:Resolved")

        assert scope == "closed"
        assert _types(patterns) == [["closed", "in"]]

    def test_an_unscoped_group_keeps_its_default_when_another_widens_the_fetch(self):
        scope, patterns = _scope("been:Blocked,closed+in:Resolved")

        assert scope == "any"
        # The first group reaches open tasks only, and now says so explicitly
        # so the client enforces it on the wider fetch
        assert _types(patterns) == [["been", "open"], ["closed", "in"]]

    def test_a_contradiction_is_left_to_the_client(self):
        scope, patterns = _scope("open+closed")

        assert scope == "any"
        assert not patterns[0].matches([], "Open")
        assert not patterns[0].matches([], "Resolved")

    def test_patterns_are_not_mutated(self):
        patterns = parse_status_patterns("been:Blocked,closed+in:Resolved", API)

        resolve_status_scope(patterns)

        assert _types(patterns) == [["been"], ["closed", "in"]]


class TestUnreachableConditions:
    def test_in_a_closed_status_under_the_open_default(self):
        patterns = parse_status_patterns("in:Resolved", API)

        assert unreachable_conditions(patterns, "open", API) == [
            ("in:Resolved", "open")
        ]

    def test_custom_closed_status_by_name(self):
        patterns = parse_status_patterns("in:Shipped", API)

        assert unreachable_conditions(patterns, "open", API) == [("in:Shipped", "open")]

    def test_in_an_open_status_under_closed(self):
        patterns = parse_status_patterns("closed+in:Open", API)

        assert unreachable_conditions(patterns, "open", API) == [("in:Open", "closed")]

    @pytest.mark.parametrize(
        "value, default",
        [
            ("any+in:Resolved", "open"),
            ("closed+in:Resolved", "open"),
            ("in:Resolved", "any"),
            ("in:Open", "open"),
            ("in:Blocked", "open"),
            ("not:in:Resolved", "open"),
            ("been:Resolved", "open"),
        ],
    )
    def test_reachable(self, value, default):
        patterns = parse_status_patterns(value, API)

        assert unreachable_conditions(patterns, default, API) == []
