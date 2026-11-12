# -*- coding: utf-8 -*-
"""`phabfive export` and `phabfive apply -f` with an edit spec, against Phorge.

The unit tests stand in for the edit engine. These do not, because the
promises worth making about an export are promises about a round trip
through a real server:

- **an export applied unchanged changes nothing** - every value is written
  in a spelling the server reads back as the value it already has;
- **an edited export sends the difference**, and applying it again sends
  nothing;
- **a task somebody else changed is not overwritten** without `--force`;
- **a create export makes a task like the old one**, whose own export
  matches the original apart from what a copy does not share.

Each test makes its own task, so nothing here depends on, or disturbs, the
seed data beyond the users and projects it names.
"""

import json
import time

import pytest

pytestmark = pytest.mark.usefixtures("live_env")

PROJECTS = ("Security", "Infrastructure")


def export(phabfive_raw, *args):
    """The exported document as data, via the JSON serialization."""
    result = phabfive_raw("--format=json", "export", *args)

    assert result.returncode == 0, result.stderr

    return json.loads(result.stdout)


def write(tmp_path, document, name="spec.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")

    return str(path)


def apply(phabfive_raw, path, *args):
    """Apply a spec and hand back (exit status, records)."""
    result = phabfive_raw("--format=json", "apply", "-f", path, *args)
    records = json.loads(result.stdout) if result.stdout.strip() else []

    return result, records


def a_task(create_task, phabfive):
    """A task with something in every list an export writes."""
    monogram, title = create_task("--tag", PROJECTS[0], "--subscribe", "tommy.svensson")
    phabfive(
        "maniphest",
        "edit",
        monogram,
        "--description",
        "First line\nSecond line with {{ a.template }}\n",
        "--assign",
        "viola.larsson",
        "--yes",
    )

    return monogram, title


def test_an_export_applied_unchanged_changes_nothing(
    create_task, phabfive, phabfive_raw
):
    """The invariant everything else rests on, through the pipe it is used with."""
    monogram, _ = a_task(create_task, phabfive)

    exported = phabfive_raw("export", monogram)
    assert exported.returncode == 0, exported.stderr
    assert exported.stdout.startswith("spec: phorge/v1alpha1\nkind: edit\n")

    result = phabfive_raw(
        "--format=json", "apply", "-f", "-", "--dry-run", input=exported.stdout
    )

    assert result.returncode == 0, result.stderr
    assert [(one["monogram"], one["status"]) for one in json.loads(result.stdout)] == [
        (monogram, "unchanged")
    ]

    # And for real: nothing is sent, so the task's modified time does not move
    before = export(phabfive_raw, monogram)["tasks"][0]["modified"]
    applied = phabfive_raw("apply", "-f", "-", input=exported.stdout)

    assert applied.returncode == 0, applied.stderr
    assert export(phabfive_raw, monogram)["tasks"][0]["modified"] == before


def test_an_export_validates_on_both_layers(
    create_task, phabfive, phabfive_raw, tmp_path
):
    monogram, _ = a_task(create_task, phabfive)
    path = tmp_path / "task.yaml"

    assert phabfive_raw("export", monogram, "-o", str(path)).returncode == 0

    result = phabfive_raw("spec", "validate", str(path))

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_edited_export_sends_the_difference_once(
    create_task, phabfive, phabfive_raw, tmp_path
):
    monogram, _ = a_task(create_task, phabfive)
    document = export(phabfive_raw, monogram)
    [item] = document["tasks"]

    item["status"] = "resolved"
    item["projects"] = [f"#{PROJECTS[1].lower()}"]
    item["subscribers"] = ["+sebastian.soderberg"]
    item["assignment"] = "@none"
    item["description"] = "Rewritten.\nStill holds {{ a.template }}\n"
    path = write(tmp_path, document)

    result, records = apply(phabfive_raw, path)

    assert result.returncode == 0, result.stderr
    assert [one["status"] for one in records] == ["edited"]
    # `Column` is there because the exported item still says `column:
    # Backlog`, and the board it now means is the one project the item
    # leaves the task tagged into
    assert {change["field"] for change in records[0]["changes"]} == {
        "Status",
        "Tags",
        "Column",
        "Assignee",
        "Subscribers",
        "Description",
    }

    after = export(phabfive_raw, monogram)["tasks"][0]

    assert after["status"] == "resolved"
    assert after["projects"] == [f"#{PROJECTS[1].lower()}"]
    assert after["assignment"] == "@none"
    assert "sebastian.soderberg" in after["subscribers"]
    assert "tommy.svensson" in after["subscribers"]  # a delta leaves the rest
    assert after["description"] == "Rewritten.\nStill holds {{ a.template }}\n"

    # The same file again. Its stamp is now older than the task, and that is
    # fine: there is nothing left to change, so the stamp is not consulted
    again, records = apply(phabfive_raw, path)

    assert again.returncode == 0, again.stderr
    assert [one["status"] for one in records] == ["unchanged"]


def test_a_task_changed_since_the_export_is_not_overwritten(
    create_task, phabfive, phabfive_raw, tmp_path
):
    monogram, _ = a_task(create_task, phabfive)
    document = export(phabfive_raw, monogram)

    # Somebody else, a moment later. The stamp is to the second, so the
    # moment has to be a whole one
    time.sleep(1.1)
    phabfive(
        "maniphest", "edit", monogram, "--subscribe", "sebastian.soderberg", "--yes"
    )

    document["tasks"][0]["status"] = "resolved"
    path = write(tmp_path, document)

    refused, records = apply(phabfive_raw, path)

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert [(one["field"], one["code"]) for one in records] == [("modified", "stale")]
    assert export(phabfive_raw, monogram)["tasks"][0]["status"] == "open"

    forced, records = apply(phabfive_raw, path, "--force")

    assert forced.returncode == 0, forced.stderr
    assert [one["status"] for one in records] == ["edited"]

    after = export(phabfive_raw, monogram)["tasks"][0]

    assert after["status"] == "resolved"
    # The full subscriber list in the file was written before the other
    # change, so forcing it took that subscriber off again: --force means
    # the file wins
    assert "sebastian.soderberg" not in after["subscribers"]


def test_every_problem_is_reported_and_nothing_is_applied(
    create_task, phabfive, phabfive_raw, tmp_path
):
    monogram, _ = a_task(create_task, phabfive)
    path = write(
        tmp_path,
        {
            "kind": "edit",
            "tasks": [
                {"task": monogram, "title": "Must not be applied"},
                {
                    "task": monogram.replace("T", "T99999"),
                    "status": "open",
                },
                {
                    "task": "T1",
                    "status": "no-such-status",
                    "subscribers": ["+nobody.x"],
                },
            ],
        },
    )

    result, records = apply(phabfive_raw, path)

    assert result.returncode == 2, result.stdout + result.stderr
    assert [(one["object"], one["field"], one["code"]) for one in records] == [
        ("tasks[1]", "task", "unknown-reference"),
        ("tasks[2]", "status", "unknown-status"),
        ("tasks[2]", "subscribers", "unknown-user"),
    ]
    assert export(phabfive_raw, monogram)["tasks"][0]["title"] != "Must not be applied"


def test_a_create_export_makes_a_task_like_the_old_one(
    create_task, phabfive, phabfive_raw
):
    monogram, _ = a_task(create_task, phabfive)

    copied = phabfive_raw("export", monogram, "--as", "create")
    assert copied.returncode == 0, copied.stderr
    assert "kind: create" in copied.stdout
    assert monogram not in copied.stdout

    created = phabfive_raw("--format=json", "apply", "-f", "-", input=copied.stdout)
    assert created.returncode == 0, created.stderr

    [record] = json.loads(created.stdout)
    assert record["status"] == "created"

    def comparable(item):
        """What a copy shares with its original: everything but who it is."""
        return {
            key: value
            for key, value in item.items()
            if key not in ("task", "modified", "parents", "subtasks", "commits")
        }

    original = export(phabfive_raw, monogram)["tasks"][0]
    copy = export(phabfive_raw, record["monogram"])["tasks"][0]

    # The one difference a create cannot avoid: whoever creates a task is
    # subscribed to it
    copy["subscribers"] = [
        name for name in copy["subscribers"] if name in original["subscribers"]
    ]

    assert comparable(copy) == comparable(original)


def test_a_search_can_be_piped_into_an_export(create_task, phabfive, phabfive_raw):
    first, title = create_task()
    second, _ = create_task()

    searched = phabfive_raw("--format=yaml", "maniphest", "show", first, second)
    assert searched.returncode == 0, searched.stderr

    result = phabfive_raw("--format=json", "export", input=searched.stdout)

    assert result.returncode == 0, result.stderr
    assert [item["task"] for item in json.loads(result.stdout)["tasks"]] == [
        first,
        second,
    ]
