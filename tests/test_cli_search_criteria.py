# -*- coding: utf-8 -*-
"""Tests for the criteria guard of maniphest search.

A bare "search" prints usage and queries nothing, so that a mistyped command
never walks the whole instance. Every filter lifts the guard, and so does
--all, which is how a script asks for every task on purpose (#419).
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.maniphest import maniphest_app

runner = CliRunner()


def _output(result):
    """Combined stdout/stderr regardless of click version."""
    output = result.output
    try:
        output += result.stderr
    except (ValueError, AttributeError):
        pass
    return output


def _invoke(args):
    mock_m = MagicMock()
    mock_m.task_search.return_value = None
    with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
        result = runner.invoke(maniphest_app, ["search", *args])
    return result, mock_m


def _invoke_with_template(search_params):
    mock_m = MagicMock()
    mock_m.task_search.return_value = None
    mock_m._load_search_config.return_value = [
        {"search": search_params, "title": None, "description": None}
    ]
    with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
        result = runner.invoke(maniphest_app, ["search", "--with", "template.yaml"])
    return result, mock_m


class TestBareSearch:
    def test_bare_search_prints_usage_and_queries_nothing(self):
        result, mock_m = _invoke([])

        assert result.exit_code == 0
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


class TestAllListsEveryTask:
    def test_all_alone_searches(self):
        result, mock_m = _invoke(["--all"])

        assert result.exit_code == 0
        assert "Usage:" not in _output(result)
        mock_m.task_search.assert_called_once()

    def test_all_alone_applies_no_filter_and_includes_closed(self):
        _, mock_m = _invoke(["--all", "--limit", "0"])

        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["include_closed"] is True
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
            "status_patterns",
            "include_task_ids",
            "exclude_task_ids",
        ):
            assert kwargs[key] is None, key

    def test_all_alone_keeps_the_default_limit(self):
        _, mock_m = _invoke(["--all"])

        assert mock_m.task_search.call_args[1]["limit"] == 100

    def test_all_in_a_template_searches(self):
        result, mock_m = _invoke_with_template({"all": True})

        assert result.exit_code == 0
        mock_m.task_search.assert_called_once()
        assert mock_m.task_search.call_args[1]["include_closed"] is True

    def test_all_false_in_a_template_does_not_search(self):
        result, mock_m = _invoke_with_template({"all": False})

        assert "Usage:" in _output(result)
        mock_m.task_search.assert_not_called()


class TestBeforeDatesAreCriteria:
    """docs/maniphest-cli.md always listed these; the guard now agrees."""

    @pytest.mark.parametrize("option", ["--created-before", "--updated-before"])
    def test_before_date_alone_searches(self, option):
        result, mock_m = _invoke([option, "1y"])

        assert result.exit_code == 0
        assert "Usage:" not in _output(result)
        mock_m.task_search.assert_called_once()
