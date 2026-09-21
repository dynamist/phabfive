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

    assert sorted(_usernames(phabfive)) == sorted(
        user["fields"]["username"] for user in everyone
    )


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


def test_an_unknown_role_exits_non_zero(phabfive_raw):
    result = phabfive_raw("user", "search", "--role=admn")

    assert result.returncode == 1
    assert "Unknown role" in result.stderr
