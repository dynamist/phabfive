# -*- coding: utf-8 -*-
"""End-to-end tests of `phabfive user search` against a live Phorge (#428).

The seed creates a bot, deploy.bot, and a disabled account,
former.employee, so that both roles exist to be filtered on.
"""

# python std lib
import json
import unicodedata

# 3rd party imports
import pytest


def _folded(text):
    """Text as the server's nameLike compares it: no case, no accents.

    Spelled out here rather than imported from phabfive.user, so that an
    assertion about what a search matched is not checked with the same
    code that decided it.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _records(phabfive, *args):
    output = phabfive("--format=jsonl", "user", "search", *args, "-l", "0")
    return [json.loads(line)["User"] for line in output.splitlines()]


def _usernames(phabfive, *args):
    return [user["Username"] for user in _records(phabfive, *args)]


def test_every_user_is_listed(phabfive, conduit):
    everyone = conduit("user.search", limit=100)["data"]

    assert sorted(_usernames(phabfive, "--role=any")) == sorted(
        user["fields"]["username"] for user in everyone
    )


def test_a_bare_search_prints_help(phabfive_raw):
    """It would otherwise read every user on the instance."""
    result = phabfive_raw("user", "search")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "--role" in result.stderr


@pytest.mark.parametrize(
    "role, expected",
    [("bot", "deploy.bot"), ("disabled", "former.employee"), ("admin", "admin")],
)
def test_a_role_the_server_filters_on(phabfive, role, expected):
    """Who holds a role is the instance's to decide, so assert the role.

    An exact list here said how many bots the instance has, which is a
    fact about the seed and not about the filter - it broke the moment a
    second bot was seeded (#497). What the filter promises is that the
    account asked for comes back and that nothing without the role does,
    and both halves fail if the constraint is ever dropped: an unfiltered
    search answers with accounts that do not hold the role.
    """
    found = _records(phabfive, f"--role={role}")

    assert expected in [user["Username"] for user in found]
    assert all(role in user["Roles"] for user in found), found


def test_every_person_who_can_use_the_instance(phabfive):
    people = _usernames(phabfive, "--not-role=bot,list,disabled")

    assert "admin" in people
    assert "deploy.bot" not in people
    assert "former.employee" not in people


def test_a_role_matched_on_the_records(phabfive):
    """activated has no user.search constraint; a disabled account lacks it."""
    assert "former.employee" in _usernames(phabfive, "--not-role=activated")
    assert "former.employee" not in _usernames(phabfive, "--role=activated")


def test_the_record_is_a_project_member_record(phabfive):
    """`user search` and `project show --show-members` compare directly."""
    admins = phabfive("user", "search", "--role=admin", json_output=True)
    [admin] = [r for r in admins if r["User"]["Username"] == "admin"]
    [project] = phabfive(
        "project", "show", "#development", "--show-members", json_output=True
    )

    member = next(m for m in project["Members"] if m["Username"] == "admin")
    assert admin["User"] == member


@pytest.mark.parametrize(
    "fragment, expected",
    [("lomqvist", "gabriel.blomqvist"), ("istrator", "admin")],
)
def test_a_query_matches_any_part_of_a_name(phabfive, fragment, expected):
    """The full-text query matched whole words only, so "holm" missed rholm.

    An exact list said the instance holds exactly one account matching the
    fragment, which is a fact about the seed rather than about the query
    (#497). Every record carrying the fragment says the same thing without
    it: a query that matched whole words, or stopped narrowing at all,
    answers with accounts that do not contain it anywhere.
    """
    found = _records(phabfive, fragment)

    assert expected in [user["Username"] for user in found]
    assert all(
        _folded(fragment) in _folded(user["Username"])
        or _folded(fragment) in _folded(user["Name"])
        for user in found
    ), found


def test_username_and_realname_each_search_one_field(phabfive):
    """The admin is "admin" by username and "Administrator" by real name."""
    by_realname = _records(phabfive, "--realname=istrator")

    assert "admin" in [user["Username"] for user in by_realname]
    assert all(_folded("istrator") in _folded(user["Name"]) for user in by_realname), (
        by_realname
    )

    # The exact list is the test here: the point is that --username finds
    # nobody at all, which containment cannot express.
    assert _usernames(phabfive, "--username=istrator") == []
    assert "admin" in _usernames(phabfive, "--username=admin")


def test_realname_ignores_accents(phabfive):
    """The seed's Sonja Bergström has a username without the accent."""
    found = _records(phabfive, "--realname=bergstrom")

    [sonja] = [user for user in found if user["Username"] == "sonja.bergstrom"]

    # Without the folding there would have been nothing to match: the real
    # name does not contain the search string as it is written.
    assert "bergstrom" not in sonja["Name"].casefold()
    assert all(_folded("bergstrom") in _folded(user["Name"]) for user in found), found


def test_an_unknown_role_exits_non_zero(phabfive_raw):
    result = phabfive_raw("user", "search", "--role=admn")

    assert result.returncode == 1
    assert "Unknown role" in result.stderr
