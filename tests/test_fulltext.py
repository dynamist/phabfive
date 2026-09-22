# -*- coding: utf-8 -*-

"""Phorge's full-text syntax, and the hint an empty text search gives."""

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app
from phabfive.fulltext import no_match_hint, substring_query

runner = CliRunner()


@pytest.mark.parametrize(
    "query, expected",
    [
        ("eam", "~eam"),
        ("sal eam", "~sal ~eam"),
        ('sal "a phrase" -ops title:x', '~sal "a phrase" -ops title:x'),
        ("~sal eam", "~sal ~eam"),
    ],
)
def test_every_plain_word_becomes_a_substring_search(query, expected):
    assert substring_query(query) == expected


@pytest.mark.parametrize("query", ["~eam", '"a phrase"', "-ops", "title:x", "", None])
def test_a_query_with_no_plain_word_is_left_alone(query):
    assert substring_query(query) is None
    assert no_match_hint(query) is None


def test_the_hint_names_the_operators_and_a_query_to_paste():
    hint = no_match_hint("sal eam")

    assert "matches whole words" in hint
    assert "'~sal ~eam'" in hint
    assert '"a phrase"' in hint
    assert "-word" in hint
    assert "title:word" in hint


class TestEmptySearches:
    """Each text search says why an empty result may be empty, on stderr."""

    def test_maniphest(self):
        maniphest = MagicMock()
        maniphest.task_search.return_value = {"tasks": []}

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["--format=json", "maniphest", "search", "robe"]
            )

        assert result.exit_code == 0
        assert "No tasks found" in result.stderr
        assert "'~robe'" in result.stderr
        assert "~robe" not in result.stdout

    def test_maniphest_without_text_says_nothing_new(self):
        maniphest = MagicMock()
        maniphest.task_search.return_value = {"tasks": []}

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(app, ["maniphest", "search", "--assigned=@me"])

        assert result.stderr == ""

    def test_paste(self):
        paste = MagicMock()
        paste.paste_search.return_value = {"pastes": []}

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(app, ["paste", "search", "inal"])

        assert "No pastes found" in result.stderr
        assert "'~inal'" in result.stderr

    def test_project(self):
        project = MagicMock()
        project.search.return_value = {"projects": []}

        with patch("phabfive.cli.project._get_project_app", return_value=project):
            result = runner.invoke(app, ["project", "search", "eam"])

        assert "No projects found" in result.stderr
        assert "'~eam'" in result.stderr

    def test_a_search_that_already_used_the_operator_gets_no_hint(self):
        project = MagicMock()
        project.search.return_value = {"projects": []}

        with patch("phabfive.cli.project._get_project_app", return_value=project):
            result = runner.invoke(app, ["project", "search", "~zzz"])

        assert result.stderr.strip() == "No projects found"
