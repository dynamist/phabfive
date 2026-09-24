# -*- coding: utf-8 -*-

"""Tests for phabfive.users: every option that takes a user takes the same four
spellings - username, @username, @me and a user PHID."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.paste import paste_app
from phabfive.exceptions import PhabfiveDataException, PhabfiveInputException
from phabfive.maniphest.core import Maniphest
from phabfive.users import resolve_user_phid, resolve_user_phids, user_list_edit

runner = CliRunner()

USERS = {"alice": "PHID-USER-alice", "bob": "PHID-USER-bob"}
CALLER = {"phid": "PHID-USER-caller", "userName": "caller"}


def _phab():
    """A client that knows alice and bob, and no user called "me"."""
    phab = MagicMock()
    phab.user.whoami.return_value = dict(CALLER)
    records = [
        {"phid": phid, "fields": {"username": name}} for name, phid in USERS.items()
    ]

    def search(constraints):
        names = {n.casefold() for n in constraints.get("usernames", [])}
        phids = set(constraints.get("phids", []))
        return {
            "data": [
                r
                for r in records
                if r["fields"]["username"].casefold() in names or r["phid"] in phids
            ]
        }

    phab.user.search.side_effect = search
    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    return maniphest


class TestResolveUserPhids:
    @pytest.mark.parametrize(
        "value", ["alice", "@alice", "ALICE", "@Alice", "PHID-USER-alice"]
    )
    def test_every_spelling_names_the_same_user(self, value):
        assert resolve_user_phid(_phab(), value) == ("PHID-USER-alice", "alice")

    def test_me_is_the_caller(self):
        assert resolve_user_phid(_phab(), "@me") == ("PHID-USER-caller", "caller")

    def test_a_mix_keeps_the_order_given(self):
        resolved = resolve_user_phids(_phab(), ["PHID-USER-bob", "@me", "alice"])

        assert list(resolved.values()) == [
            ("PHID-USER-bob", "bob"),
            ("PHID-USER-caller", "caller"),
            ("PHID-USER-alice", "alice"),
        ]

    def test_a_phid_is_looked_up_rather_than_passed_through(self):
        """A mistyped PHID in a search filter would otherwise match nothing."""
        with pytest.raises(PhabfiveDataException, match="'PHID-USER-typo'"):
            resolve_user_phid(_phab(), "PHID-USER-typo")

    def test_every_unknown_one_is_named(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            resolve_user_phids(_phab(), ["nosuch", "alice", "PHID-USER-typo"])

        assert str(excinfo.value) == "No such user: 'nosuch', 'PHID-USER-typo'"

    def test_one_lookup_per_spelling(self):
        phab = _phab()

        resolve_user_phids(phab, ["alice", "@bob", "PHID-USER-alice"])

        assert phab.user.search.call_count == 2


class TestManiphest:
    @pytest.mark.parametrize("value", ["@alice", "PHID-USER-alice"])
    def test_search_filters(self, value):
        maniphest = _maniphest(_phab())

        assert maniphest._resolve_user_filter_phids(value, "assigned to") == [
            "PHID-USER-alice"
        ]

    def test_create(self):
        maniphest = _maniphest(_phab())

        result = maniphest.create_task(
            "A task",
            assignee="PHID-USER-alice",
            subscribers="@bob,PHID-USER-alice",
            dry_run=True,
        )

        assert result["assignee"] == "alice"
        assert result["subscribers"] == ["bob", "alice"]

    def test_edit(self):
        maniphest = _maniphest(_phab())
        task = {
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

        transactions, _ = maniphest.build_task_edit(
            "42", task, assign="@alice", subscribe="PHID-USER-bob,bob"
        )

        assert {"type": "owner", "value": "PHID-USER-alice"} in transactions
        assert {"type": "subscribers.add", "value": ["PHID-USER-bob"]} in transactions


class TestPaste:
    @pytest.mark.parametrize("value", ["@alice", "PHID-USER-alice"])
    def test_search_author(self, value):
        instance = MagicMock()
        instance.phab = _phab()
        instance.paste_search.return_value = {"pastes": []}

        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(paste_app, ["search", f"--author={value}"])

        assert result.exit_code == 0, result.output
        constraints = instance.paste_search.call_args.kwargs["constraints"]
        assert constraints == {"authors": ["PHID-USER-alice"]}

    def test_create_subscribers_are_sent_as_usernames(self):
        instance = MagicMock()
        instance.phab = _phab()
        instance.create_paste_from_content.return_value = {"id": 42}

        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app,
                [
                    "create",
                    "Notes",
                    "--content=hello",
                    "--subscribe=PHID-USER-alice,@bob,alice",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = instance.create_paste_from_content.call_args.kwargs
        assert kwargs["subscribers"] == ["alice", "bob"]


def _task(subscribers):
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
            "subscribers": {"subscriberPHIDs": list(subscribers)},
        },
    }


class TestUserListEdit:
    """Adding and removing users on a list sends only what changes."""

    def _edit(self, current, add=(), remove=()):
        phab = _phab()
        return user_list_edit(
            "subscribers",
            "Subscribers",
            current,
            added=resolve_user_phids(phab, list(add)),
            removed=resolve_user_phids(phab, list(remove)),
        )

    def test_nothing_asked_is_nothing_sent(self):
        assert self._edit(["PHID-USER-alice"]) == ([], [])

    def test_only_a_subscriber_is_removed(self):
        transactions, changes = self._edit(["PHID-USER-alice"], remove=["alice", "bob"])

        assert transactions == [
            {"type": "subscribers.remove", "value": ["PHID-USER-alice"]}
        ]
        assert changes == [
            {"field": "Subscribers", "old": None, "new": "Removed: alice"}
        ]

    def test_only_a_non_subscriber_is_added(self):
        transactions, _ = self._edit(["PHID-USER-alice"], add=["alice", "@bob"])

        assert transactions == [{"type": "subscribers.add", "value": ["PHID-USER-bob"]}]

    def test_add_and_remove_in_one_edit(self):
        transactions, changes = self._edit(
            ["PHID-USER-alice"], add=["bob"], remove=["alice"]
        )

        assert transactions == [
            {"type": "subscribers.add", "value": ["PHID-USER-bob"]},
            {"type": "subscribers.remove", "value": ["PHID-USER-alice"]},
        ]
        assert [change["new"] for change in changes] == [
            "Added: bob",
            "Removed: alice",
        ]

    def test_two_spellings_of_one_user_are_sent_once(self):
        transactions, _ = self._edit([], add=["alice", "PHID-USER-alice", "@alice"])

        assert transactions == [
            {"type": "subscribers.add", "value": ["PHID-USER-alice"]}
        ]

    def test_the_same_user_added_and_removed_is_refused(self):
        """However each was spelled: a name and a PHID are one user."""
        with pytest.raises(PhabfiveInputException, match="Cannot both add and remove"):
            self._edit([], add=["alice"], remove=["PHID-USER-alice"])


class TestTaskSubscribers:
    def test_remove(self):
        maniphest = _maniphest(_phab())

        transactions, changes = maniphest.build_task_edit(
            "42",
            _task(["PHID-USER-alice", "PHID-USER-caller"]),
            unsubscribe="@me,bob",
        )

        assert transactions == [
            {"type": "subscribers.remove", "value": ["PHID-USER-caller"]}
        ]
        assert changes[-1]["new"] == "Removed: caller"

    def test_removing_a_non_subscriber_changes_nothing(self):
        maniphest = _maniphest(_phab())

        transactions, _ = maniphest.build_task_edit(
            "42", _task([]), unsubscribe=["alice"]
        )

        assert transactions == []

    def test_add_and_remove_the_same_user_is_refused(self):
        maniphest = _maniphest(_phab())

        with pytest.raises(PhabfiveInputException):
            maniphest.build_task_edit(
                "42", _task([]), subscribe=["alice"], unsubscribe=["@alice"]
            )


class TestPasteSubscribers:
    def _paste(self, subscribers):
        from phabfive.paste import Paste

        with patch("phabfive.paste.core.Phabfive.__init__", return_value=None):
            paste = Paste()
        paste.phab = _phab()
        paste.phab.paste.search.return_value = {
            "data": [
                {
                    "id": 7,
                    "phid": "PHID-PSTE-7",
                    "fields": {"title": "Notes", "language": "text"},
                    "attachments": {
                        "content": {"content": "hello"},
                        "subscribers": {"subscriberPHIDs": list(subscribers)},
                    },
                }
            ],
            "cursor": {"after": None},
        }
        return paste

    def test_an_edit_that_changes_nothing_sends_nothing(self):
        paste = self._paste(["PHID-USER-alice"])

        paste.edit_paste(7, subscribers=["@alice"], unsubscribers=["bob"])

        paste.phab.paste.edit.assert_not_called()

    def test_edit_diffs_against_the_current_subscribers(self):
        paste = self._paste(["PHID-USER-alice"])

        result = paste.edit_paste(7, subscribers=["bob"], unsubscribers=["alice"])

        paste.phab.paste.edit.assert_called_once_with(
            objectIdentifier="P7",
            transactions=[
                {"type": "subscribers.add", "value": ["PHID-USER-bob"]},
                {"type": "subscribers.remove", "value": ["PHID-USER-alice"]},
            ],
        )
        assert [change["new"] for change in result["changes"]] == [
            "Added: bob",
            "Removed: alice",
        ]

    def test_nothing_to_change_says_so(self):
        paste = self._paste([])

        result = paste.edit_paste(7, unsubscribers=["alice"], dry_run=True)

        assert result["changes"] == []
        assert result["message"] == "No changes (already at target state)"
