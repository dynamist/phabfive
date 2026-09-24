# -*- coding: utf-8 -*-
"""Tests for the criteria guard and the status scope of maniphest search.

A bare "search" prints usage and queries nothing, so that a mistyped command
never walks the whole instance. Every filter lifts the guard, and so does
--status=any, which is how a script asks for every task on purpose (#419).
--all is the deprecated spelling of the same request.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.maniphest import maniphest_app
from phabfive.transitions import parse_status_patterns

runner = CliRunner()


def _output(result):
    """Combined stdout/stderr regardless of click version."""
    output = result.output
    try:
        output += result.stderr
    except (ValueError, AttributeError):
        pass
    return output


def _mock_maniphest():
    mock_m = MagicMock()
    mock_m.task_search.return_value = None
    # The real parser, so the CLI sees real StatusPattern objects
    mock_m.parse_status_patterns_with_api.side_effect = parse_status_patterns
    return mock_m


def _invoke(args):
    mock_m = _mock_maniphest()
    with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
        result = runner.invoke(maniphest_app, ["search", *args])
    return result, mock_m


def _invoke_with_template(search_params):
    mock_m = _mock_maniphest()
    mock_m._load_search_config.return_value = [
        {"search": search_params, "title": None, "description": None}
    ]
    with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
        result = runner.invoke(maniphest_app, ["search", "--with", "template.yaml"])
    return result, mock_m


def _conditions(mock_m):
    patterns = mock_m.task_search.call_args[1]["status_patterns"]
    return [[c["type"] for c in p.conditions] for p in patterns]


class TestBareSearch:
    def test_bare_search_prints_help_and_queries_nothing(self):
        result, mock_m = _invoke([])

        assert result.exit_code == 2
        assert "Usage:" in _output(result)
        mock_m.task_search.assert_not_called()

    @pytest.mark.parametrize(
        "args",
        [["--limit", "0"], ["--show-policy"], ["--order", "created"]],
    )
    def test_options_that_filter_nothing_do_not_lift_the_guard(self, args):
        result, mock_m = _invoke(args)

        assert "Usage:" in _output(result)
        mock_m.task_search.assert_not_called()


class TestStatusScopeKeywords:
    @pytest.mark.parametrize("keyword", ["open", "closed", "any"])
    def test_a_scope_keyword_alone_searches(self, keyword):
        result, mock_m = _invoke(["--status", keyword])

        assert result.exit_code == 0
        assert "Usage:" not in _output(result)
        assert _conditions(mock_m) == [[keyword]]
        assert mock_m.task_search.call_args[1]["include_closed"] is False

    def test_any_alone_applies_no_other_filter(self):
        _, mock_m = _invoke(["--status", "any", "--limit", "0"])

        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["limit"] == 0
        for key in (
            "text_query",
            "tag",
            "assigned",
            "author",
            "space",
            "created_after",
            "created_before",
            "updated_after",
            "updated_before",
            "column_patterns",
            "priority_patterns",
            "include_task_ids",
            "exclude_task_ids",
        ):
            assert kwargs[key] is None, key

    def test_a_keyword_ands_with_a_pattern(self):
        _, mock_m = _invoke(["--status", "any+in:Resolved"])

        assert _conditions(mock_m) == [["any", "in"]]

    def test_status_any_in_a_template_searches(self):
        result, mock_m = _invoke_with_template({"status": "any"})

        assert result.exit_code == 0
        assert _conditions(mock_m) == [["any"]]


class TestDeprecatedAll:
    def test_all_alone_still_searches_and_warns(self):
        result, mock_m = _invoke(["--all"])

        assert result.exit_code == 0
        assert "--all is deprecated, use --status=any instead." in _output(result)
        mock_m.task_search.assert_called_once()
        assert mock_m.task_search.call_args[1]["include_closed"] is True

    def test_all_keeps_the_default_limit(self):
        _, mock_m = _invoke(["--all"])

        assert mock_m.task_search.call_args[1]["limit"] == 100

    def test_all_with_a_pattern_is_still_accepted(self):
        result, mock_m = _invoke(["--all", "--status", "in:Resolved"])

        assert result.exit_code == 0
        assert mock_m.task_search.call_args[1]["include_closed"] is True

    def test_all_with_status_any_is_accepted(self):
        result, mock_m = _invoke(["--all", "--status", "any"])

        assert result.exit_code == 0
        mock_m.task_search.assert_called_once()

    @pytest.mark.parametrize("status", ["open", "closed", "closed+in:Resolved"])
    def test_all_with_a_narrower_scope_is_refused(self, status):
        result, mock_m = _invoke(["--all", "--status", status])

        assert result.exit_code == 1
        assert "--all cannot be combined with --status" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_all_is_hidden_from_help(self):
        result = runner.invoke(maniphest_app, ["search", "--help"])

        assert "--all" not in result.output
        assert "any+in:Resolved" in result.output

    def test_all_in_a_template_searches_and_warns(self):
        result, mock_m = _invoke_with_template({"all": True})

        assert result.exit_code == 0
        assert "'all: true' in a search template is deprecated" in _output(result)
        assert mock_m.task_search.call_args[1]["include_closed"] is True

    def test_all_false_in_a_template_does_not_search_or_warn(self):
        result, mock_m = _invoke_with_template({"all": False})

        assert "Usage:" in _output(result)
        # Named, not the bare word: `--with` itself is deprecated since #486
        # and says so on every run, which is a different sentence.
        assert "in a search template is deprecated" not in _output(result)
        mock_m.task_search.assert_not_called()


class TestBeforeDatesAreCriteria:
    """docs/maniphest-cli.md always listed these; the guard now agrees."""

    @pytest.mark.parametrize("option", ["--created-before", "--updated-before"])
    def test_before_date_alone_searches(self, option):
        result, mock_m = _invoke([option, "1y"])

        assert result.exit_code == 0
        assert "Usage:" not in _output(result)
        mock_m.task_search.assert_called_once()
