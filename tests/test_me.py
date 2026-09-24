# -*- coding: utf-8 -*-

"""Tests for `@me`, whoever is running the command.

Every option that takes a user resolves `@me` through phabfive.me, so they all
agree on what it means: the caller, on every instance, including one that has
a user whose username is "me". A username does not take a keyword away from
everybody else (#496).

The `@` is what makes it a keyword, and this is the one place in phabfive
where the sigil carries meaning - `alice` and `@alice` are the same user
everywhere. A bare `me` is therefore an ordinary username lookup, which is how
the account called "me" stays reachable. Both halves of that promise are
tested here, because either one alone is a trap.

The policy options have their own tests in test_policy.py.
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


def _assert_caller(phids):
    """`@me` resolved to the caller and not to the user called "me"."""
    assert phids == ["PHID-USER-caller"], phids


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

    def test_a_user_called_me_does_not_take_the_keyword(self):
        """The point of #496: the account does not get to own the word."""
        phab = _phab(user_called_me=True)

        assert whoami_me(phab, option="--assigned") == CALLER

    def test_resolving_it_asks_only_whoami(self):
        """It used to search for a user called "me" on every resolution.

        That request existed only to raise the ambiguity error, so dropping
        the error drops a round trip from every command taking a user.
        """
        phab = _phab(user_called_me=True)

        resolve_me(phab)

        phab.user.whoami.assert_called_once()
        phab.user.search.assert_not_called()

    def test_whoami_without_a_phid_is_an_error(self):
        phab = _phab()
        phab.user.whoami.return_value = {"userName": "caller"}

        with pytest.raises(PhabfiveDataException, match="no PHID for you"):
            whoami_me(phab)

    def test_a_failing_lookup_is_not_a_traceback(self):
        phab = _phab()
        phab.user.whoami.side_effect = RuntimeError("boom")

        with pytest.raises(PhabfiveDataException, match="boom"):
            whoami_me(phab)


class TestManiphestSearch:
    @pytest.mark.parametrize("option", ["--assigned", "--author"])
    def test_a_user_called_me_does_not_take_the_keyword(self, option):
        maniphest = _maniphest(_phab(user_called_me=True))

        phids = maniphest._resolve_user_filter_phids("@me", "by", option=option)

        _assert_caller(phids)

    def test_a_bare_me_is_the_user_called_me(self):
        """The escape hatch, and the only place the sigil decides anything."""
        maniphest = _maniphest(_phab(user_called_me=True))

        phids = maniphest._resolve_user_filter_phids("me", "by", option="--author")

        assert phids == ["PHID-USER-me"]

    def test_the_caller_otherwise(self):
        maniphest = _maniphest(_phab())

        assert maniphest._resolve_user_filter_phids("@me", "assigned to") == [
            "PHID-USER-caller"
        ]


class TestManiphestCreate:
    """A write is where guessing wrong would have cost the most (#496)."""

    def test_assign(self):
        maniphest = _maniphest(_phab(user_called_me=True))

        result = maniphest.create_task("A task", assignee="@me", dry_run=True)

        # The preview names who it resolved to, which is the caller and not
        # the account called "me"
        assert "caller" in result["assignee"]
        assert result["assignee"] != "me"

    def test_subscribe(self):
        maniphest = _maniphest(_phab(user_called_me=True))

        result = maniphest.create_task("A task", subscribers=["@me"], dry_run=True)

        assert [s for s in result["subscribers"] if "caller" in s]
        assert "me" not in result["subscribers"]


class TestManiphestEdit:
    def test_assign(self):
        maniphest = _maniphest(_phab(user_called_me=True))

        transactions, _ = maniphest.build_task_edit("42", _task(), assign="@me")

        assert {"type": "owner", "value": "PHID-USER-caller"} in transactions

    def test_a_bare_me_edits_to_the_user_called_me(self):
        maniphest = _maniphest(_phab(user_called_me=True))

        transactions, _ = maniphest.build_task_edit("42", _task(), assign="me")

        assert {"type": "owner", "value": "PHID-USER-me"} in transactions

    def test_the_caller_otherwise(self):
        maniphest = _maniphest(_phab())

        transactions, _ = maniphest.build_task_edit("42", _task(), assign="@Me")

        assert {"type": "owner", "value": "PHID-USER-caller"} in transactions


class TestProjectMembers:
    def test_a_user_called_me_does_not_take_the_keyword(self):
        assert resolve_user_phids(
            _phab(user_called_me=True), ["@me"], option="--join"
        ) == {"@me": ("PHID-USER-caller", "caller")}

    def test_both_spellings_in_one_call_are_two_people(self):
        """The sigil is the whole difference, so ask for both at once."""
        resolved = resolve_user_phids(_phab(user_called_me=True), ["@me", "me"])

        assert resolved["@me"][0] == "PHID-USER-caller"
        assert resolved["me"][0] == "PHID-USER-me"

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

        assert result.exit_code == 0, result.output
        instance.paste_search.assert_called_once()
        constraints = instance.paste_search.call_args.kwargs["constraints"]
        assert constraints["authors"] == ["PHID-USER-caller"]

    def test_create_subscribe(self):
        instance = self._app()

        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app,
                ["create", "Notes", "--content=hello", "--subscribe=@me", "--yes"],
            )

        assert result.exit_code == 0, result.output
        instance.create_paste_from_content.assert_called_once()
