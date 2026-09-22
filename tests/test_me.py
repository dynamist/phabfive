# -*- coding: utf-8 -*-

"""Tests for `@me`, whoever is running the command.

Every option that takes a user resolves `@me` through phabfive.me, so they all
refuse it the same way on an instance that has a user called "me". The policy
options have their own tests in test_policy.py.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.paste import paste_app
from phabfive.exceptions import PhabfiveDataException
from phabfive.maniphest.core import Maniphest
from phabfive.me import is_me, resolve_me, whoami_me
from phabfive.users import resolve_user_phids

runner = CliRunner()

CALLER = {"phid": "PHID-USER-caller", "userName": "caller"}
USER_ME = {"phid": "PHID-USER-me", "fields": {"username": "me"}}


def _phab(user_called_me=False):
    """A client whose caller is "caller", with or without a user called "me"."""
    phab = MagicMock()
    phab.user.whoami.return_value = dict(CALLER)
    phab.user.search.return_value = {"data": [USER_ME] if user_called_me else []}
    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    return maniphest


def _task():
    return {
        "id": 42,
        "phid": "PHID-TASK-42",
        "fields": {
            "name": "A task",
            "status": {"name": "Open", "value": "open"},
            "priority": {"name": "High", "value": 80},
            "description": {"raw": ""},
            "ownerPHID": None,
        },
        "attachments": {
            "columns": {"boards": {}},
            "projects": {"projectPHIDs": []},
            "subscribers": {"subscriberPHIDs": []},
        },
    }


def _assert_ambiguous(excinfo, option):
    """The error names the option, and both PHIDs on lines of their own."""
    message = str(excinfo.value)
    assert message.startswith(f"{option}: @me is ambiguous")
    lines = [line.split() for line in message.splitlines()[1:]]
    assert lines == [
        ["PHID-USER-me", "me"],
        ["PHID-USER-caller", "caller", "(you)"],
    ]


class TestIsMe:
    @pytest.mark.parametrize("value", ["@me", "@ME", "@Me", " @me "])
    def test_any_case_is_me(self, value):
        """Phorge usernames are case-insensitive, so @Me is not somebody."""
        assert is_me(value)

    @pytest.mark.parametrize("value", ["me", "@meg", "@m", "", None, "PHID-USER-me"])
    def test_anything_else_is_not(self, value):
        assert not is_me(value)


class TestWhoamiMe:
    def test_the_caller(self):
        assert whoami_me(_phab()) == CALLER
        assert resolve_me(_phab()) == "PHID-USER-caller"

    def test_a_user_called_me_makes_it_an_error(self):
        phab = _phab(user_called_me=True)

        with pytest.raises(PhabfiveDataException) as excinfo:
            whoami_me(phab, option="--assigned")

        _assert_ambiguous(excinfo, "--assigned")

    def test_whoami_without_a_phid_is_an_error(self):
        phab = _phab()
        phab.user.whoami.return_value = {"userName": "caller"}

        with pytest.raises(PhabfiveDataException, match="no PHID for you"):
            whoami_me(phab)

    def test_a_failing_lookup_is_not_a_traceback(self):
        phab = _phab()
        phab.user.search.side_effect = RuntimeError("boom")

        with pytest.raises(PhabfiveDataException, match="boom"):
            whoami_me(phab)


class TestManiphestSearch:
    @pytest.mark.parametrize("option", ["--assigned", "--author"])
    def test_a_user_called_me_makes_it_an_error(self, option):
        maniphest = _maniphest(_phab(user_called_me=True))

        with pytest.raises(PhabfiveDataException) as excinfo:
            maniphest._resolve_user_filter_phids("alice,@me", "by", option=option)

        _assert_ambiguous(excinfo, option)

    def test_the_caller_otherwise(self):
        maniphest = _maniphest(_phab())

        assert maniphest._resolve_user_filter_phids("@me", "assigned to") == [
            "PHID-USER-caller"
        ]


class TestManiphestCreate:
    def test_assign(self):
        maniphest = _maniphest(_phab(user_called_me=True))

        with pytest.raises(PhabfiveDataException) as excinfo:
            maniphest.create_task("A task", assignee="@me", dry_run=True)

        _assert_ambiguous(excinfo, "--assign")

    def test_subscribe(self):
        maniphest = _maniphest(_phab(user_called_me=True))

        with pytest.raises(PhabfiveDataException) as excinfo:
            maniphest.create_task("A task", subscribers=["@me"], dry_run=True)

        _assert_ambiguous(excinfo, "--subscribe")


class TestManiphestEdit:
    def test_assign(self):
        maniphest = _maniphest(_phab(user_called_me=True))

        with pytest.raises(PhabfiveDataException) as excinfo:
            maniphest.build_task_edit("42", _task(), assign="@me")

        _assert_ambiguous(excinfo, "--assign")

    def test_subscribe(self):
        maniphest = _maniphest(_phab(user_called_me=True))

        with pytest.raises(PhabfiveDataException) as excinfo:
            maniphest.build_task_edit("42", _task(), subscribe="@me")

        _assert_ambiguous(excinfo, "--subscribe")

    def test_the_caller_otherwise(self):
        maniphest = _maniphest(_phab())

        transactions, _ = maniphest.build_task_edit("42", _task(), assign="@Me")

        assert {"type": "owner", "value": "PHID-USER-caller"} in transactions


class TestProjectMembers:
    def test_a_user_called_me_makes_it_an_error(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            resolve_user_phids(
                _phab(user_called_me=True), ["@me"], option="--add-member"
            )

        _assert_ambiguous(excinfo, "--add-member")

    def test_the_caller_otherwise(self):
        assert resolve_user_phids(_phab(), ["@me"]) == {
            "@me": ("PHID-USER-caller", "caller")
        }


class TestPaste:
    def _app(self):
        instance = MagicMock()
        instance.phab = _phab(user_called_me=True)
        return instance

    def test_search_author(self):
        instance = self._app()

        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(paste_app, ["search", "--author=@me"])

        assert result.exit_code != 0
        assert isinstance(result.exception, PhabfiveDataException)
        assert "--author: @me is ambiguous" in str(result.exception)
        instance.paste_search.assert_not_called()

    def test_create_subscribe(self):
        instance = self._app()

        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app,
                ["create", "Notes", "--content=hello", "--subscribe=@me"],
            )

        assert result.exit_code != 0
        assert "--subscribe: @me is ambiguous" in str(result.exception)
        instance.create_paste_from_content.assert_not_called()
