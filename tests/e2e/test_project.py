# -*- coding: utf-8 -*-
"""End-to-end tests of `phabfive project` against a live Phorge (#418)."""

# python std lib
import json
import uuid

# 3rd party imports
import pytest

from tests.phorge_versions import (
    missing_before,
    phorge_older_than,
)


@pytest.fixture
def create_project(phabfive):
    """Create a project with a unique name, and answer with its record.

    Conduit can neither delete nor archive a project, so nothing is cleaned
    up: the "e2e-" prefix is what tells these apart on a shared instance,
    and every test here asserts on its own projects rather than on a count
    of all of them.
    """

    def create(*args):
        name = f"e2e-{uuid.uuid4().hex[:8]}"
        [record] = phabfive("project", "create", name, *args, json_output=True)
        return record

    return create


def _hashtag(record):
    return record["Project"]["Hashtag"]


def _id(record):
    return record["Link"].rstrip("/").rsplit("/", 1)[-1]


def _count_projects(conduit):
    """Every project on the instance, counted by paging project.search directly.

    No status constraint, which is every status - and the only way to ask a
    Phabricator, which has none.
    """
    total = 0
    after = None

    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        result = conduit("project.search", **params)
        total += len(result["data"])
        after = (result.get("cursor") or {}).get("after")
        if not after:
            return total


def test_create_answers_with_the_show_record(create_project):
    record = create_project("--icon=tag", "--color=green", "--description=From e2e")

    assert record["Project"]["Icon"] == "tag"
    assert record["Project"]["Color"] == "green"
    assert record["Project"]["Description"] == "From e2e"
    assert _hashtag(record).startswith("#e2e-")


def test_members_are_listed_with_their_roles(create_project, phabfive):
    """What tells a bot from a person in a membership check (T3284)."""
    record = create_project("--member=@me,@viola.larsson")

    [shown] = phabfive(
        "project", "show", _hashtag(record), "--show-members", json_output=True
    )

    members = {member["Username"]: member for member in shown["Members"]}
    assert set(members) == {"admin", "viola.larsson"}
    assert "admin" in members["admin"]["Roles"]
    assert "bot" not in members["viola.larsson"]["Roles"]


def test_policies_are_set_and_named(create_project, phabfive):
    # A member, or restricting the view policy to the project would lock
    # the caller out and be refused
    record = create_project("--joinable-by=admin", "--member=@me")
    hashtag = _hashtag(record)

    phabfive("project", "edit", hashtag, f"--visible-to={hashtag}")

    [shown] = phabfive("project", "show", hashtag, "--show-policy", json_output=True)

    assert shown["Policy"] == {
        "Visible To": hashtag,
        "Editable By": "All Users",
        "Joinable By": "Administrators",
    }


def test_edit_adds_and_removes_members_and_keeps_hashtags(
    create_project, phabfive, conduit
):
    record = create_project("--slug=first-" + uuid.uuid4().hex[:6])
    hashtag = _hashtag(record)
    second = "second-" + uuid.uuid4().hex[:6]

    phabfive(
        "project",
        "edit",
        hashtag,
        "--join=@viola.larsson,@mikael.wallin",
        f"--add-slug={second}",
    )
    phabfive("project", "edit", hashtag, "--leave=@mikael.wallin")

    [shown] = phabfive("project", "show", hashtag, "--show-members", json_output=True)
    assert [member["Username"] for member in shown["Members"]] == ["viola.larsson"]

    slugs = next(
        iter(conduit("project.query", **{"ids[0]": _id(record)})["data"].values())
    )["slugs"]
    assert second in slugs
    assert any(slug.startswith("first-") for slug in slugs)


def test_an_edit_that_changes_nothing_says_so(create_project, phabfive_raw):
    record = create_project("--color=blue")

    result = phabfive_raw(
        "--format=json", "project", "edit", _hashtag(record), "--color=blue"
    )

    assert result.returncode == 0
    assert "No changes" in result.stderr
    assert json.loads(result.stdout)[0]["Project"]["Color"] == "blue"


@missing_before(
    "2026.27",
    "project.edit crashes creating a milestone, looking the parent up by the PHID "
    "of a project it has not loaded yet",
)
def test_subprojects_and_milestones(create_project, phabfive):
    parent = create_project()
    hashtag = _hashtag(parent)

    [sub] = phabfive(
        "project",
        "create",
        f"e2e-sub-{uuid.uuid4().hex[:6]}",
        f"--parent={hashtag}",
        json_output=True,
    )
    [milestone] = phabfive(
        "project", "create", "Sprint 1", f"--milestone-of={hashtag}", json_output=True
    )

    assert sub["Project"]["Parent"] == parent["Project"]["Name"]
    assert milestone["Project"]["Milestone"] == 1
    assert milestone["Project"]["Hashtag"] is None

    found = phabfive(
        "project", "search", f"--parent={hashtag}", "--milestones", json_output=True
    )
    assert [record["Link"] for record in found] == [milestone["Link"]]


def test_a_shared_name_is_refused_with_the_ids(phabfive_raw):
    """The seed has a "Sprint 1" under Development and another under QA."""
    result = phabfive_raw("project", "show", "Sprint 1")

    assert result.returncode == 1
    assert "ambiguous" in result.stderr


def test_a_taken_hashtag_is_refused_on_a_dry_run(phabfive_raw):
    result = phabfive_raw("project", "create", "Development", "--dry-run")

    assert result.returncode == 1
    assert "same hashtag" in result.stderr


def test_a_self_lockout_is_a_sentence(create_project, phabfive_raw):
    record = create_project()

    result = phabfive_raw("project", "edit", _hashtag(record), "--editable-by=no-one")

    assert result.returncode == 1
    assert "Nothing was changed" in result.stderr


def test_audit_lists_every_project_one_per_line(phabfive_raw, conduit):
    """`phabfive --format=jsonl project search --status=any --space='*' --show-policy -l 0`"""
    result = phabfive_raw(
        "--format=jsonl",
        "project",
        "search",
        "--status=any",
        "--space=*",
        "--show-policy",
        "-l",
        "0",
    )

    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]

    assert len(records) == _count_projects(conduit)
    assert all(
        set(record["Policy"]) == {"Visible To", "Editable By", "Joinable By"}
        for record in records
    )


#: The seeded Development board as it shows, per phorge/seed/data/projects.json:
#: its columns, then the column of its milestone. "Won't Do" is hidden.
DEVELOPMENT_COLUMNS = ["Backlog", "Up Next", "In Progress", "In Review", "Done"]

no_column_order = missing_before(
    "2023.32",
    "project.column.search reports no sequence, isHidden or isDefaultColumn",
)


@no_column_order
def test_show_columns_lists_the_board_in_order(phabfive):
    [record] = phabfive(
        "project", "show", "#development", "--show-columns", json_output=True
    )

    columns = record["Columns"]
    assert [column["Name"] for column in columns] == [
        *DEVELOPMENT_COLUMNS,
        "Sprint 1",
    ]
    assert columns[0]["Default"] is True
    assert columns[-1]["Proxy"].startswith("Sprint 1 (ID ")
    assert not any(column["Hidden"] for column in columns)


@no_column_order
def test_show_hidden_adds_the_hidden_column(phabfive):
    [record] = phabfive(
        "project", "show", "#development", "--show-hidden", json_output=True
    )

    hidden = [column["Name"] for column in record["Columns"] if column["Hidden"]]
    assert hidden == ["Won't Do"]


def test_show_columns_of_a_project_with_no_workboard(phabfive_raw):
    """The teams are seeded without workboards."""
    result = phabfive_raw(
        "--format=json", "project", "show", "#management_team", "--show-columns"
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)[0]["Columns"] is None
    assert "#management_team has no workboard" in result.stderr


@no_column_order
def test_column_steps_past_neither_hidden_nor_milestone_columns(
    create_task, phabfive, phabfive_raw
):
    """forward from Done stays put; a wrong name lists the board in order."""
    task_id, _ = create_task("--tag", "Development")

    def column():
        [task] = phabfive("maniphest", "show", task_id, json_output=True)
        return task["Boards"]["Development"]["Column"]

    phabfive("edit", task_id, "--tag=Development", "--column=Done")
    phabfive("edit", task_id, "--tag=Development", "--column=forward")
    assert column() == "Done"

    phabfive("edit", task_id, "--tag=Development", "--column=Won't Do")
    assert column() == "Won't Do"
    phabfive("edit", task_id, "--tag=Development", "--column=backward")
    assert column() == "Done"

    result = phabfive_raw("edit", task_id, "--tag=Development", "--column=Review")
    assert result.returncode != 0
    assert str([*DEVELOPMENT_COLUMNS, "Sprint 1"]) in result.stderr
    assert "--show-columns" in result.stderr


@pytest.mark.skipif(
    not phorge_older_than("2023.32"), reason="this server reports column order"
)
def test_column_step_is_refused_without_column_order(
    create_task, phabfive, phabfive_raw
):
    """Where the order is unknown a step is refused, and a name still works."""
    task_id, _ = create_task("--tag", "Development")

    result = phabfive_raw("edit", task_id, "--tag=Development", "--column=forward")
    assert result.returncode != 0
    assert "does not report the order" in result.stderr

    phabfive("edit", task_id, "--tag=Development", "--column=Done")
    [task] = phabfive("maniphest", "show", task_id, json_output=True)
    assert task["Boards"]["Development"]["Column"] == "Done"
