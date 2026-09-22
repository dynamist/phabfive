# -*- coding: utf-8 -*-

"""Tests for `maniphest search --author`.

Phorge's maniphest.search names its author constraint "authorPHIDs",
while paste.search calls the equivalent filter "authors". Sending the
wrong key fails with ERR-INVALID-CONSTRAINT, so the key is pinned here.

`--author` mirrors `--assigned`: both accept "@me", a username, or a
comma-separated list for OR logic, and both refuse a name that cannot be
resolved rather than silently returning no matches.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.maniphest import maniphest_app
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.maniphest.core import Maniphest

runner = CliRunner()

ADMIN_PHID = "PHID-USER-1234567890abcdefghij"
ALICE_PHID = "PHID-USER-aaaaaaaaaaaaaaaaaaaa"
BOB_PHID = "PHID-USER-bbbbbbbbbbbbbbbbbbbb"

USER_PHIDS = {"alice": ALICE_PHID, "bob": BOB_PHID}


def _maniphest():
    """A Maniphest with a mocked API client, ready for task_search."""
    maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = "https://phabricator.example.com"
    maniphest.conf = {"PHAB_SPACE": "S1"}

    maniphest.phab.phid.lookup.return_value = {
        "S1": {
            "phid": "PHID-SPCE-1",
            "name": "Global",
            "fullName": "Global",
            "uri": "/S1",
        }
    }
    maniphest.phab.user.whoami.return_value = {
        "phid": ADMIN_PHID,
        "userName": "admin",
    }
    # No user is called "me", which would make @me ambiguous
    maniphest.phab.user.search.return_value = {"data": []}

    response = MagicMock()
    response.response = {"data": []}
    response.get.return_value = {"after": None}
    maniphest.phab.maniphest.search.return_value = response
    maniphest.phab.project.query.return_value = {"data": {}}

    maniphest._resolve_user_phid = MagicMock(side_effect=USER_PHIDS.get)
    maniphest._get_open_statuses = MagicMock(return_value=["open"])

    return maniphest


def _constraints(maniphest):
    """Constraints of the first maniphest.search call."""
    return maniphest.phab.maniphest.search.call_args_list[0][1]["constraints"]


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestAuthorConstraint:
    """task_search turns --author into the authorPHIDs constraint."""

    def test_me_shortcut_resolves_via_whoami(self, mock_init):
        maniphest = _maniphest()

        maniphest.task_search(author="@me")

        assert _constraints(maniphest)["authorPHIDs"] == [ADMIN_PHID]

    def test_username_resolves_to_phid(self, mock_init):
        maniphest = _maniphest()

        maniphest.task_search(author="alice")

        assert _constraints(maniphest)["authorPHIDs"] == [ALICE_PHID]

    def test_comma_separated_authors_give_or_logic(self, mock_init):
        maniphest = _maniphest()

        maniphest.task_search(author="@me,alice,bob")

        assert _constraints(maniphest)["authorPHIDs"] == [
            ADMIN_PHID,
            ALICE_PHID,
            BOB_PHID,
        ]

    def test_constraint_is_not_pastes_authors_key(self, mock_init):
        """paste.search uses "authors"; sending that here would fail."""
        maniphest = _maniphest()

        maniphest.task_search(author="alice")

        assert "authors" not in _constraints(maniphest)

    def test_author_is_sufficient_search_criteria(self, mock_init):
        """--author alone must not raise "No search criteria specified"."""
        maniphest = _maniphest()

        maniphest.task_search(author="@me")

        assert maniphest.phab.maniphest.search.called

    def test_author_combines_with_assigned(self, mock_init):
        maniphest = _maniphest()

        maniphest.task_search(author="alice", assigned="bob")

        constraints = _constraints(maniphest)
        assert constraints["authorPHIDs"] == [ALICE_PHID]
        assert constraints["assigned"] == [BOB_PHID]

    def test_no_author_adds_no_constraint(self, mock_init):
        maniphest = _maniphest()

        maniphest.task_search(assigned="alice")

        assert "authorPHIDs" not in _constraints(maniphest)


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestUnresolvableUser:
    """An unknown name is an error, not an empty result set.

    Returning no tasks made a typo look like "nothing matched".
    """

    def test_unknown_author_raises(self, mock_init):
        maniphest = _maniphest()

        with pytest.raises(
            PhabfiveConfigException, match="User 'nosuchuser' not found"
        ):
            maniphest.task_search(author="nosuchuser")

        maniphest.phab.maniphest.search.assert_not_called()

    def test_unknown_assignee_raises(self, mock_init):
        maniphest = _maniphest()

        with pytest.raises(
            PhabfiveConfigException, match="User 'nosuchuser' not found"
        ):
            maniphest.task_search(assigned="nosuchuser")

        maniphest.phab.maniphest.search.assert_not_called()

    def test_one_bad_name_in_a_list_raises(self, mock_init):
        maniphest = _maniphest()

        with pytest.raises(
            PhabfiveConfigException, match="User 'nosuchuser' not found"
        ):
            maniphest.task_search(author="alice,nosuchuser")

    def test_whoami_without_phid_raises(self, mock_init):
        maniphest = _maniphest()
        maniphest.phab.user.whoami.return_value = {"userName": "admin"}

        with pytest.raises(PhabfiveDataException, match="no PHID for you"):
            maniphest.task_search(author="@me")


class TestAuthorCliPlumbing:
    """The CLI option reaches task_search and reports failures cleanly."""

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_author_is_passed_to_task_search(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_search.return_value = {"tasks": [], "project_names": {}}
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["search", "--author", "@me"])

        assert result.exit_code == 0
        assert mock_m.task_search.call_args[1]["author"] == "@me"

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_author_alone_does_not_print_usage(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_search.return_value = {"tasks": [], "project_names": {}}
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["search", "--author", "alice"])

        assert result.exit_code == 0
        assert "Usage:" not in result.output
        mock_m.task_search.assert_called_once()

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_unknown_author_exits_one_without_traceback(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_search.side_effect = PhabfiveConfigException(
            "User 'nosuchuser' not found"
        )
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["search", "--author", "nosuchuser"])

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_unknown_assignee_exits_one(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_search.side_effect = PhabfiveConfigException(
            "User 'nosuchuser' not found"
        )
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["search", "--assigned", "nosuchuser"])

        assert result.exit_code == 1
