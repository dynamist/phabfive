# -*- coding: utf-8 -*-

"""`@none`: no assignee, for `--assigned` and `--assign` only (#513).

It is a keyword the way `@me` is: the sigil makes it one, so it means
nobody even on an instance with a user called "none", and the bare `none`
is that user. Every other option that takes a user refuses it.
"""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.cli import completers
from phabfive.cli.completers import complete_assignee_filter, complete_user_list_filter
from phabfive.exceptions import PhabfiveConfigException, PhabfiveInputException
from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.validators import validate_assignment
from phabfive.me import NONE_DATASOURCE, is_none
from phabfive.policy import validate_policy_value
from phabfive.spec import Spec, validate_offline, validate_online
from phabfive.spec.online import ManiphestUserResolver, index_references
from phabfive.users import resolve_user_phids

USER_NONE = {"phid": "PHID-USER-none", "fields": {"username": "none"}}
ALICE = {"phid": "PHID-USER-alice", "fields": {"username": "alice"}}


def _phab():
    """A client that knows alice and a user called "none"."""
    phab = MagicMock()
    phab.user.whoami.return_value = {"phid": "PHID-USER-caller", "userName": "caller"}

    def search(constraints):
        names = {name.casefold() for name in constraints.get("usernames", [])}
        phids = set(constraints.get("phids", []))
        return {
            "data": [
                user
                for user in (USER_NONE, ALICE)
                if user["fields"]["username"] in names or user["phid"] in phids
            ]
        }

    phab.user.search.side_effect = search
    return phab


def _maniphest(phab=None):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab or _phab()
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}
    return maniphest


def _task(owner):
    return {
        "id": 42,
        "phid": "PHID-TASK-42",
        "fields": {
            "name": "A task",
            "status": {"name": "Open", "value": "open"},
            "priority": {"name": "High", "value": 80},
            "description": {"raw": ""},
            "ownerPHID": owner,
        },
        "attachments": {
            "columns": {"boards": {}},
            "projects": {"projectPHIDs": []},
            "subscribers": {"subscriberPHIDs": []},
        },
    }


class TestIsNone:
    @pytest.mark.parametrize("value", ["@none", "@NONE", "@None", " @none "])
    def test_any_case_is_none(self, value):
        assert is_none(value)

    @pytest.mark.parametrize("value", ["none", "@nonet", "@me", "", None])
    def test_anything_else_is_not(self, value):
        assert not is_none(value)


class TestAssignedFilter:
    def test_none_is_the_datasource_function(self):
        phab = _phab()

        phids = _maniphest(phab)._resolve_user_filter_phids(
            "@none", "assigned to", option="--assigned", allow_none=True
        )

        assert phids == [NONE_DATASOURCE]
        # Nobody needs no lookup
        phab.user.search.assert_not_called()

    def test_none_mixes_with_users_in_the_order_given(self):
        phids = _maniphest()._resolve_user_filter_phids(
            "alice,@none,@me", "assigned to", option="--assigned", allow_none=True
        )

        assert phids == ["PHID-USER-alice", NONE_DATASOURCE, "PHID-USER-caller"]

    def test_a_bare_none_is_the_user_called_none(self):
        phids = _maniphest()._resolve_user_filter_phids(
            "none", "assigned to", option="--assigned", allow_none=True
        )

        assert phids == ["PHID-USER-none"]

    @pytest.mark.parametrize("option", ["--author", "--subscriber", "--closed-by"])
    def test_other_filters_refuse_it(self, option):
        phab = _phab()

        with pytest.raises(PhabfiveInputException, match=f"{option}: @none"):
            _maniphest(phab)._resolve_user_filter_phids(
                "alice,@none", "by", option=option
            )

        # Refused, not looked up as the user called "none"
        phab.user.search.assert_not_called()

    def test_task_search_sends_it_to_the_server(self):
        """Only --assigned is resolved with `allow_none`; the first call stops
        the search, which is all this needs to see."""

        class Stop(Exception):
            pass

        maniphest = _maniphest()

        with patch.object(
            maniphest, "_resolve_user_filter_phids", side_effect=Stop
        ) as resolve:
            with pytest.raises(Stop):
                maniphest.task_search(assigned="@none")

        assert resolve.call_args.args == ("@none", "assigned to")
        assert resolve.call_args.kwargs == {"option": "--assigned", "allow_none": True}


class TestUserOptions:
    def test_the_resolver_refuses_it(self):
        with pytest.raises(PhabfiveInputException, match="--subscribe: @none"):
            resolve_user_phids(_phab(), ["alice", "@none"], option="--subscribe")

    def test_create_assign_refuses_it(self):
        with pytest.raises(PhabfiveInputException, match="--assign: @none"):
            _maniphest()._resolve_users(["@none"], option="--assign")


class TestEditAssign:
    def test_none_clears_the_assignee(self):
        transactions, changes = _maniphest().build_task_edit(
            "42", _task("PHID-USER-alice"), assign="@none"
        )

        assert transactions == [{"type": "owner", "value": None}]
        assert changes == [{"field": "Assignee", "old": "alice", "new": "(none)"}]

    def test_an_unassigned_task_changes_nothing(self):
        transactions, changes = _maniphest().build_task_edit(
            "42", _task(None), assign="@none"
        )

        assert (transactions, changes) == ([], [])

    def test_with_unassign_is_redundant_not_a_conflict(self):
        validate_assignment("@none", True)

        transactions, _ = _maniphest().build_task_edit(
            "42", _task("PHID-USER-alice"), assign="@none", unassign=True
        )

        assert transactions == [{"type": "owner", "value": None}]

    def test_a_bare_none_assigns_the_user_called_none(self):
        transactions, _ = _maniphest().build_task_edit("42", _task(None), assign="none")

        assert transactions == [{"type": "owner", "value": "PHID-USER-none"}]

    def test_a_user_with_unassign_is_still_a_conflict(self):
        with pytest.raises(PhabfiveInputException, match="--unassign"):
            validate_assignment("none", True)


class TestPolicy:
    def test_refused_with_a_pointer_to_no_one(self):
        with pytest.raises(PhabfiveConfigException, match="no-one"):
            validate_policy_value("@none", option="--visible-to")


def _search_spec(search):
    return Spec.from_data(
        {"kind": "search", "searches": [{"search": search}]}, source="<test>"
    )


def _app(phab):
    return _maniphest(phab)


class TestSpec:
    def test_assigned_takes_it_offline(self):
        assert validate_offline(_search_spec({"assigned": "@none,alice"})) == []

    @pytest.mark.parametrize("key", ["author", "subscriber", "closed-by"])
    def test_other_user_keys_refuse_it_offline(self, key):
        [problem] = validate_offline(_search_spec({key: "@none"}))

        assert (problem.field, problem.code) == (f"search.{key}", "unknown-value")

    def test_assigned_takes_it_online_without_a_lookup(self):
        phab = _phab()

        assert validate_online(_search_spec({"assigned": "@none"}), _app(phab)) == []
        phab.user.search.assert_not_called()

    def test_it_is_not_a_reference_in_assigned(self):
        index = index_references(_search_spec({"assigned": "@none,alice"}))

        assert [ref.value for ref in index.references] == ["alice"]

    def test_the_user_resolver_refuses_it(self):
        answers = ManiphestUserResolver().resolve(_app(_phab()), ["@none", "none"])

        assert answers["@none"].problem == "unknown-user"
        assert answers["none"].phid == "PHID-USER-none"


def _complete(completer, incomplete):
    phab = MagicMock()
    phab.user.search.return_value = {"data": [], "cursor": {"after": None}}
    with patch.object(
        completers,
        "_get_values_with_api_fallback",
        side_effect=lambda fetch, default: fetch(phab),
    ):
        return completer(incomplete)


class TestCompletion:
    def test_assigned_offers_it_before_anything_is_typed(self):
        assert _complete(complete_assignee_filter, "") == [
            ("@me", "yourself"),
            ("@none", "nobody"),
        ]

    @pytest.mark.parametrize("incomplete", ["@n", "@none"])
    def test_assigned_offers_it_for_what_was_typed(self, incomplete):
        assert _complete(complete_assignee_filter, incomplete) == [("@none", "nobody")]

    def test_assigned_offers_it_after_a_comma(self):
        assert ("@me,@none", "nobody") in _complete(complete_assignee_filter, "@me,")

    def test_other_filters_do_not_offer_it(self):
        assert ("@none", "nobody") not in _complete(complete_user_list_filter, "")
