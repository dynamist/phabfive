# -*- coding: utf-8 -*-
"""End-to-end tests of `phabfive user search` against a live Phorge (#428).

The seed creates a bot, deploy.bot, and a disabled account,
former.employee, so that both roles exist to be filtered on.
"""

# python std lib
import json


def _usernames(phabfive, *args):
    output = phabfive("--format=jsonl", "user", "search", *args, "-l", "0")
    return [json.loads(line)["User"]["Username"] for line in output.splitlines()]


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


def test_a_role_the_server_filters_on(phabfive):
    assert _usernames(phabfive, "--role=bot") == ["deploy.bot"]
    assert _usernames(phabfive, "--role=disabled") == ["former.employee"]


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
    [admin] = phabfive("user", "search", "--role=admin", json_output=True)
    [project] = phabfive(
        "project", "show", "#development", "--show-members", json_output=True
    )

    member = next(m for m in project["Members"] if m["Username"] == "admin")
    assert admin["User"] == member


def test_a_query_matches_any_part_of_a_name(phabfive):
    """The full-text query matched whole words only, so "holm" missed rholm."""
    assert _usernames(phabfive, "lomqvist") == ["gabriel.blomqvist"]
    assert _usernames(phabfive, "istrator") == ["admin"]


def test_username_and_realname_each_search_one_field(phabfive):
    """The admin is "admin" by username and "Administrator" by real name."""
    assert _usernames(phabfive, "--realname=istrator") == ["admin"]
    assert _usernames(phabfive, "--username=istrator") == []
    assert "admin" in _usernames(phabfive, "--username=admin")


def test_realname_ignores_accents(phabfive):
    """The seed's Sonja Bergström has a username without the accent."""
    assert _usernames(phabfive, "--realname=bergstrom") == ["sonja.bergstrom"]


def test_an_unknown_role_exits_non_zero(phabfive_raw):
    result = phabfive_raw("user", "search", "--role=admn")

    assert result.returncode == 1
    assert "Unknown role" in result.stderr
