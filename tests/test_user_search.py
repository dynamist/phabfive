# -*- coding: utf-8 -*-

"""`phabfive user search`: listing users and filtering them by role (#428)."""

# python std lib
import json

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException
from phabfive.user import User

runner = CliRunner()

URL = "http://phorge.localhost"


def _user(uid, username, roles):
    return {
        "id": uid,
        "phid": f"PHID-USER-{username}",
        "fields": {
            "username": username,
            "realName": username.title(),
            "roles": list(roles),
            "dateCreated": 1789875812,
            "dateModified": 1789875812,
        },
    }


HUMAN = ["verified", "approved", "activated"]

USERS = [
    _user(1, "admin", ["admin", *HUMAN]),
    _user(2, "viola", HUMAN),
    _user(3, "deploybot", ["bot", "verified", "approved", "activated"]),
    _user(4, "gone", ["disabled", "verified", "approved"]),
    _user(5, "pending", ["verified"]),
    _user(6, "ops-list", ["list", "approved", "activated"]),
]

ROLE_FLAGS = {
    "isAdmin": "admin",
    "isBot": "bot",
    "isDisabled": "disabled",
    "isMailingList": "list",
}


class FakeUserSearch:
    """user.search, paging at `page_size` and honouring the role constraints."""

    def __init__(self, users, page_size=100):
        self.users = users
        self.page_size = page_size
        self.calls = []

    def __call__(self, constraints=None, limit=100, after=None, **_):
        constraints = constraints or {}
        self.calls.append({"constraints": constraints, "limit": limit, "after": after})
        found = self.users

        for flag, role in ROLE_FLAGS.items():
            if flag in constraints:
                found = [
                    user
                    for user in found
                    if (role in user["fields"]["roles"]) == constraints[flag]
                ]
        if "query" in constraints:
            found = [
                u for u in found if constraints["query"] in u["fields"]["username"]
            ]

        start = int(after or 0)
        size = min(limit, self.page_size)
        more = start + size < len(found)

        return {
            "data": found[start : start + size],
            "cursor": {"after": str(start + size) if more else None},
        }


def _app(page_size=100):
    with patch("phabfive.user.Phabfive.__init__", return_value=None):
        user = User()

    user.phab = MagicMock()
    user.phab.user.search.side_effect = FakeUserSearch(USERS, page_size=page_size)
    user.url = URL
    user.format_link = lambda url, text: url

    return user


def _names(records):
    return [record["User"]["Username"] for record in records]


def _search(user):
    return user.phab.user.search.side_effect


@pytest.fixture
def restore_output_format():
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


class TestSearch:
    def test_everyone_sorted_by_username(self):
        assert _names(_app().search()) == [
            "admin",
            "deploybot",
            "gone",
            "ops-list",
            "pending",
            "viola",
        ]

    def test_the_record_matches_a_project_member(self):
        """Username, Name and Roles, so the two outputs compare directly."""
        [record] = _app().search(roles=["admin"])

        assert record["_url"] == f"{URL}/p/admin/"
        assert record["User"] == {
            "Username": "admin",
            "Name": "Admin",
            "Roles": ["admin", *HUMAN],
        }
        assert "Metadata" not in record

    def test_metadata_when_asked_for(self):
        [record] = _app().search(roles=["admin"], show_metadata=True)

        assert record["Metadata"]["PHID"] == "PHID-USER-admin"
        assert record["Metadata"]["ID"] == 1

    def test_every_person_who_can_use_the_instance(self):
        """What the T3284 preflight diffs against a project's members."""
        user = _app()

        records = user.search(not_roles=["bot", "list", "disabled"])

        assert _names(records) == ["admin", "pending", "viola"]
        assert _search(user).calls[0]["constraints"] == {
            "isBot": False,
            "isMailingList": False,
            "isDisabled": False,
        }

    def test_every_role_asked_for_is_required(self):
        assert _names(_app().search(roles=["verified", "activated"])) == [
            "admin",
            "deploybot",
            "viola",
        ]

    def test_a_role_without_a_constraint_is_matched_on_the_records(self):
        user = _app()

        records = user.search(not_roles=["activated"])

        assert _names(records) == ["gone", "pending"]
        assert _search(user).calls[0]["constraints"] == {}

    def test_a_limit_counts_matches_not_rows_fetched(self):
        """Filtering locally means the server cannot be asked for a limit."""
        user = _app(page_size=2)

        records = user.search(roles=["activated"], limit=2)

        assert len(records) == 2
        assert all(call["limit"] == 100 for call in _search(user).calls)

    def test_a_limit_is_forwarded_when_the_server_filters_everything(self):
        user = _app()

        user.search(roles=["admin"], limit=5)

        assert _search(user).calls[0]["limit"] == 5

    def test_every_page_is_read(self):
        user = _app(page_size=2)

        assert len(user.search()) == len(USERS)
        assert len(_search(user).calls) == 3

    def test_a_query_is_sent_as_is(self):
        user = _app()

        user.search(query="vio")

        assert _search(user).calls[0]["constraints"] == {"query": "vio"}

    def test_an_unknown_role_is_refused(self):
        user = _app()

        with pytest.raises(PhabfiveConfigException, match="Unknown role 'admn'"):
            user.search(roles=["admn"])

        user.phab.user.search.assert_not_called()

    def test_a_role_both_required_and_excluded_is_refused(self):
        with pytest.raises(PhabfiveConfigException, match="both require and exclude"):
            _app().search(roles=["bot"], not_roles=["bot"])


class TestCli:
    def _invoke(self, args, user=None):
        with patch("phabfive.user.User", return_value=user or _app()):
            return runner.invoke(app, args)

    def test_jsonl_is_one_user_per_line(self, restore_output_format):
        result = self._invoke(
            [
                "--format=jsonl",
                "user",
                "search",
                "--not-role=bot,list",
                "--not-role=disabled",
            ]
        )

        assert result.exit_code == 0, result.output
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        assert [line["User"]["Username"] for line in lines] == [
            "admin",
            "pending",
            "viola",
        ]

    def test_nothing_found_leaves_stdout_empty(self, restore_output_format):
        result = self._invoke(["--format=json", "user", "search", "nobody"])

        assert result.exit_code == 0
        assert result.stdout == ""
        assert "No users found" in result.stderr

    def test_an_unknown_role_exits_non_zero(self):
        result = self._invoke(["user", "search", "--role=admn"])

        assert result.exit_code == 1
        assert "Unknown role" in result.stderr
