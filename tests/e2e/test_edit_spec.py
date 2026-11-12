# -*- coding: utf-8 -*-
"""`phabfive apply -f` with a hand-written edit spec, against Phorge.

The unit tests stand in for the edit engine. These do not, because what an
edit spec promises is about a real server:

- **only the difference is sent**, and applying the file again sends nothing;
- **a spec that states what the task already is plans no change** - which is
  also how these tests read a task back: by stating what it should be;
- **a task modified after the item's stamp is not overwritten** without
  `--force`;
- **every problem is reported and nothing is applied** when any item has one.

Each test makes its own task, so nothing here depends on, or disturbs, the
seed data beyond the users and projects it names.
"""

import json

import pytest

pytestmark = pytest.mark.usefixtures("live_env")

PROJECTS = ("Security", "Infrastructure")

#: A stamp older than any task these tests make.
LONG_AGO = "2001-01-01T00:00:00Z"


def write(tmp_path, *items, name="spec.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps({"kind": "edit", "interpolate": False, "tasks": list(items)}),
        encoding="utf-8",
    )

    return str(path)


def apply(phabfive_raw, path, *args):
    """Apply a spec and hand back (result, records)."""
    result = phabfive_raw("--format=json", "apply", "-f", path, *args)
    records = json.loads(result.stdout) if result.stdout.strip() else []

    return result, records


def statuses(phabfive_raw, path, *args):
    result, records = apply(phabfive_raw, path, *args)

    assert result.returncode == 0, result.stdout + result.stderr

    return [one["status"] for one in records]


def a_task(create_task):
    monogram, _ = create_task("--tag", PROJECTS[0], "--subscribe", "tommy.svensson")

    return monogram


def test_an_edit_sends_the_difference_once(create_task, phabfive_raw, tmp_path):
    monogram = a_task(create_task)
    item = {
        "task": monogram,
        "status": "resolved",
        "projects": [f"#{PROJECTS[1].lower()}"],
        "subscribers": ["+sebastian.soderberg"],
        "assignment": "viola.larsson",
        "description": "Rewritten.\nStill holds {{ a.template }}\n",
    }
    path = write(tmp_path, item)

    result, records = apply(phabfive_raw, path, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert [one["status"] for one in records] == ["would-edit"]

    result, records = apply(phabfive_raw, path)

    assert result.returncode == 0, result.stderr
    assert [one["status"] for one in records] == ["edited"]
    assert {change["field"] for change in records[0]["changes"]} == {
        "Status",
        "Tags",
        "Assignee",
        "Subscribers",
        "Description",
    }

    # The same file again: nothing is left to change
    assert statuses(phabfive_raw, path) == ["unchanged"]

    # A delta left the other subscriber alone, and a full list replaced the
    # tags: stating exactly that plans no change either. The creator of a
    # task is subscribed to it, so the subscribers are stated as a delta
    stated = write(
        tmp_path,
        {
            "task": monogram,
            "status": "resolved",
            "projects": [f"#{PROJECTS[1].lower()}"],
            "subscribers": ["+tommy.svensson", "+sebastian.soderberg"],
            "assignment": "viola.larsson",
        },
        name="stated.json",
    )

    assert statuses(phabfive_raw, stated, "--dry-run") == ["unchanged"]


def test_a_spec_is_read_from_standard_input(create_task, phabfive_raw):
    monogram = a_task(create_task)
    document = json.dumps(
        {"kind": "edit", "tasks": [{"task": monogram, "status": "open"}]}
    )

    result = phabfive_raw(
        "--format=json", "apply", "-f", "-", "--dry-run", input=document
    )

    assert result.returncode == 0, result.stderr
    assert [(one["monogram"], one["status"]) for one in json.loads(result.stdout)] == [
        (monogram, "unchanged")
    ]


def test_a_task_modified_after_the_stamp_is_not_overwritten(
    create_task, phabfive_raw, tmp_path
):
    monogram = a_task(create_task)
    path = write(
        tmp_path, {"task": monogram, "modified": LONG_AGO, "status": "resolved"}
    )

    refused, records = apply(phabfive_raw, path)

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert [(one["field"], one["code"]) for one in records] == [("modified", "stale")]

    # Nothing was written: the task is still open
    still_open = write(
        tmp_path, {"task": monogram, "status": "open"}, name="still-open.json"
    )
    assert statuses(phabfive_raw, still_open, "--dry-run") == ["unchanged"]

    assert statuses(phabfive_raw, path, "--force") == ["edited"]

    # The stamp is only consulted when the item would change something, so
    # the same file is not refused for the modification it made itself
    assert statuses(phabfive_raw, path) == ["unchanged"]


def test_every_problem_is_reported_and_nothing_is_applied(
    create_task, phabfive_raw, tmp_path
):
    monogram = a_task(create_task)
    path = write(
        tmp_path,
        {"task": monogram, "status": "resolved"},
        {"task": monogram.replace("T", "T99999"), "status": "open"},
        {"task": "T1", "status": "no-such-status", "subscribers": ["+nobody.x"]},
    )

    result, records = apply(phabfive_raw, path)

    assert result.returncode == 2, result.stdout + result.stderr
    assert [(one["object"], one["field"], one["code"]) for one in records] == [
        ("tasks[1]", "task", "unknown-reference"),
        ("tasks[2]", "status", "unknown-status"),
        ("tasks[2]", "subscribers", "unknown-user"),
    ]

    still_open = write(
        tmp_path, {"task": monogram, "status": "open"}, name="still-open.json"
    )
    assert statuses(phabfive_raw, still_open, "--dry-run") == ["unchanged"]


def test_an_edit_spec_validates_on_both_layers(create_task, phabfive_raw, tmp_path):
    monogram = a_task(create_task)
    path = write(tmp_path, {"task": monogram, "priority": "high"})

    result = phabfive_raw("spec", "validate", path)

    assert result.returncode == 0, result.stdout + result.stderr
