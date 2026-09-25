# -*- coding: utf-8 -*-

"""A `--tag` naming no project is an error, not an empty search (#523).

It used to be logged and answered with no tasks and exit status 0, so a
script could not tell a misspelled tag from a search that found nothing.
Inside a `,` pattern it was worse, since `--tag a,typo` could have read as
a search of `a` alone. `resolve_project_phids` raises now, and
`maniphest search` answers with one line and exit status 1.
"""

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app
from phabfive.exceptions import PhabfiveInputException, PhabfiveNotFoundException
from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.resolvers import resolve_project_phids

runner = CliRunner()

KNOWN = "PHID-PROJ-known"


def _phab():
    """An instance with one project, `known`."""
    phab = MagicMock()

    def search(constraints):
        if constraints.get("slugs") == ["known"]:
            return {"data": [{"phid": KNOWN, "fields": {"name": "Known"}}]}
        return {"data": []}

    phab.project.search.side_effect = search
    phab.project.query.return_value = {
        "data": {KNOWN: {"name": "Known", "slugs": ["known"]}}
    }
    return phab


class TestResolver:
    def test_a_known_project_resolves(self):
        assert resolve_project_phids(_phab(), "known") == [KNOWN]

    def test_an_unknown_project_is_not_found(self):
        with pytest.raises(PhabfiveNotFoundException, match="'nosuch' not found"):
            resolve_project_phids(_phab(), "nosuch")

    def test_a_close_miss_names_the_likely_project(self):
        with pytest.raises(PhabfiveNotFoundException, match="Did you mean: Known"):
            resolve_project_phids(_phab(), "knwon")

    def test_a_wildcard_matching_nothing_is_not_found(self):
        with pytest.raises(PhabfiveNotFoundException, match="matched no projects"):
            resolve_project_phids(_phab(), "nosuch*")

    def test_no_name_is_bad_input(self):
        with pytest.raises(PhabfiveInputException):
            resolve_project_phids(_phab(), "")


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestTaskSearch:
    def _maniphest(self):
        maniphest = Maniphest()
        maniphest.phab = _phab()
        maniphest.url = "https://phorge.example.com"
        maniphest.conf = {}
        maniphest._get_open_statuses = MagicMock(return_value=["open"])
        return maniphest

    @pytest.mark.parametrize("tag", ["nosuch", "known,nosuch", "known+nosuch"])
    def test_an_unknown_tag_raises_before_searching(self, _init, tag):
        maniphest = self._maniphest()

        with pytest.raises(PhabfiveNotFoundException, match="nosuch"):
            maniphest.task_search(tag=tag)

        maniphest.phab.maniphest.search.assert_not_called()

    def test_a_malformed_pattern_is_bad_input(self, _init):
        with pytest.raises(PhabfiveInputException, match="Invalid tag pattern"):
            self._maniphest().task_search(tag=",")


class TestCli:
    @pytest.mark.parametrize(
        "error",
        [
            PhabfiveNotFoundException("Project 'nosuch' not found"),
            PhabfiveInputException("Invalid tag pattern 'a+': Empty project name"),
        ],
    )
    def test_an_unresolvable_tag_exits_1_with_one_line(self, error):
        maniphest = MagicMock()
        maniphest.task_search.side_effect = error

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["--format=json", "maniphest", "search", "--tag=nosuch"]
            )

        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == f"ERROR: {error}\n"
