# -*- coding: utf-8 -*-
"""Tests for `phabfive apply -f` (#486).

Four things are pinned here.

**It dispatches on the spec's `kind:`.** A search spec handed to `apply` is
answered with one sentence naming `phabfive search -f`, not with a schema
error about keys it does not have - and the sentence is a *usage* refusal, so
it never appears among the problem records a `--format=json` reader parses.

**Nothing is written until both layers have passed.** The offline layer runs
in the command and the online one inside the planner, and a failure in either
leaves with a status that says which: 1 for the file, 2 for the instance.
A run that reaches neither - because there is no instance to ask - is 3.

**Exit 4 exists because Conduit has no transactions.** A spec whose third
object is refused has left two real objects behind, and "nothing happened"
and "half of it happened" are the two answers a caller retrying most needs to
tell apart. Both have a fixture here.

**A top-level `apply` does not collide with the monogram shortcut.**
`phabfive T123` still expands with the command registered, and `phabfive
apply -f x.yaml` is left alone.
"""

# python std lib
import json
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest
from typer.testing import CliRunner

# phabfive imports
import phabfive.create
from phabfive.cli import app, preprocess_monograms
from phabfive.cli import spec_run
from phabfive.core import Phabfive
from phabfive.spec.create import (
    CreateItem,
    CreatePlan,
    CreatePlanError,
    CreateRecord,
)
from phabfive.exceptions import (
    PhabfiveConnectionException,
    PhabfiveDataException,
)

runner = CliRunner()

# Captured at import, before `never_builds_an_app` patches the attribute for
# every test: this is the real function, for the one test that exercises it.
REAL_INSTANCE = spec_run._instance

# A create spec with nothing wrong in it and nothing an instance would be
# asked about, so the offline layer passes and the planner is the only thing
# a test has to stand in for.
CREATE = """\
spec: phorge/v1alpha1
kind: create
metadata:
  description: One task
tasks:
  - id: first
    title: Set up CI
"""

# The other kind, declared, so nothing has to be inferred.
SEARCH = """\
spec: phorge/v1alpha1
kind: search
searches:
  - search:
      limit: 10
"""

# A variable nothing declares and a value of the wrong type: two problems, so
# "reports everything, not the first" has something to say, and both are
# offline, so no instance is needed to find them.
BROKEN = """\
kind: create
tasks:
  - title: "{{ nobody }}"
    priority: 7
"""


@pytest.fixture(autouse=True)
def restore_output_format():
    """Put the class-level output format back, even when a test fails.

    `Phabfive._output_format` is class state that `_setup_output_options`
    writes, so a test that leaves it set changes what every later test in
    the suite renders.
    """
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


@pytest.fixture(autouse=True)
def never_builds_an_app():
    """Fail loudly if a test reaches the instance without saying it means to.

    Every test that wants the online half patches this itself; anything else
    touching it is a command that went to the network before it had checked
    the file.
    """
    with patch.object(
        spec_run,
        "_instance",
        side_effect=AssertionError("the command constructed an app"),
    ) as never:
        yield never


def write(tmp_path, text, name="spec.yaml"):
    """Put a spec on disk and give back the path as a string."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def invoke(*args):
    """Run the root app, so the global --format callback runs too."""
    return runner.invoke(app, list(args))


def an_instance():
    """An app the planner can be handed, which asks nothing."""
    instance = MagicMock()
    instance.conf = {"PHAB_URL": "https://phorge.example.com/api/"}
    return instance


def a_plan(*, anchor=False):
    """The plan one task makes, with no instance involved in making it."""
    items = [
        CreateItem(
            object_type="task",
            path="tasks[0]",
            local_id="first",
            transactions=({"type": "title", "value": "Set up CI"},),
            display={"title": "Set up CI"},
        )
    ]

    if anchor:
        items.insert(
            0,
            CreateItem(
                object_type="task",
                path="tasks[0].parent",
                anchor=True,
                phids=("PHID-TASK-one",),
                display={"title": "T1"},
            ),
        )

    return CreatePlan(items=tuple(items), source="spec.yaml")


def a_record(status="created", **overrides):
    """One `CreateRecord`, of the shape `apply_plan` yields."""
    fields = {
        "object_type": "task",
        "path": "tasks[0]",
        "status": status,
        "local_id": "first",
        "phid": "PHID-TASK-first",
        "id": 123,
        "monogram": "T123",
        "title": "Set up CI",
    }
    fields.update(overrides)
    return CreateRecord(**fields)


class TestDispatchOnKind:
    """The wrong kind is a sentence naming the right command, not a schema error."""

    def test_a_search_spec_is_refused_by_name(self, tmp_path, never_builds_an_app):
        path = write(tmp_path, SEARCH)

        result = invoke("--format=rich", "apply", "-f", path)

        assert result.exit_code == 1
        assert "is a search spec" in result.stderr
        assert f"phabfive search -f {path}" in result.stderr
        never_builds_an_app.assert_not_called()

    def test_the_refusal_is_not_a_problem_record(self, tmp_path):
        """A usage refusal never enters a --format=json problem stream."""
        result = invoke("--format=json", "apply", "-f", write(tmp_path, SEARCH))

        assert result.exit_code == 1
        assert result.stdout.strip() == ""
        assert "is a search spec" in result.stderr

    def test_the_long_form_of_the_flag_is_spec(self, tmp_path):
        result = invoke("--format=rich", "apply", "--spec", write(tmp_path, SEARCH))

        assert result.exit_code == 1
        assert "is a search spec" in result.stderr


class TestExitStatus:
    """One fixture per code in the table, including the two `apply` adds."""

    def test_a_clean_run_is_zero(self, tmp_path):
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(phabfive.create, "plan_spec", return_value=a_plan()):
                with patch.object(
                    phabfive.create, "apply_plan", return_value=iter([a_record()])
                ):
                    result = invoke(
                        "--format=rich", "apply", "-f", write(tmp_path, CREATE)
                    )

        assert result.exit_code == 0
        assert "created" in result.stdout
        assert "T123" in result.stdout

    def test_a_dry_run_is_zero_and_writes_nothing(self, tmp_path):
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(phabfive.create, "plan_spec", return_value=a_plan()):
                with patch.object(phabfive.create, "apply_plan") as never:
                    result = invoke(
                        "--format=rich",
                        "apply",
                        "-f",
                        write(tmp_path, CREATE),
                        "--dry-run",
                    )

        assert result.exit_code == 0
        assert "[DRY RUN]" in result.stdout
        assert "would create 1 task" in result.stdout
        assert "task $first 'Set up CI'" in result.stdout
        never.assert_not_called()

    def test_an_offline_failure_is_one(self, tmp_path, never_builds_an_app):
        result = invoke("--format=json", "apply", "-f", write(tmp_path, BROKEN))

        assert result.exit_code == 1
        assert sorted(one["code"] for one in json.loads(result.stdout)) == [
            "undefined-variable",
            "wrong-type",
        ]
        never_builds_an_app.assert_not_called()

    def test_a_file_that_cannot_be_read_is_one(self, tmp_path, never_builds_an_app):
        result = invoke(
            "--format=json", "apply", "-f", str(tmp_path / "nothing-here.yaml")
        )

        assert result.exit_code == 1
        assert [one["code"] for one in json.loads(result.stdout)] == ["unreadable"]
        never_builds_an_app.assert_not_called()

    def test_a_spec_holding_something_nothing_creates_is_one(self, tmp_path):
        """The document's own shape, so it is the file that is wrong.

        Named after a passphrase rather than a paste: a paste is created
        from a spec since the builder landed, and a test that pinned the
        status of a refusal by naming a type that is no longer refused
        reads as a promise it is not making. Nothing creates a passphrase
        and nothing will - Phorge publishes no `passphrase.edit`.
        """
        refusal = CreatePlanError(
            "passphrases[0]: nothing creates a 'passphrase' from a spec yet",
            check="unsupported-type",
        )

        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(phabfive.create, "plan_spec", side_effect=refusal):
                result = invoke("--format=rich", "apply", "-f", write(tmp_path, CREATE))

        assert result.exit_code == 1
        assert "nothing creates a 'passphrase'" in result.stderr

    def test_a_reference_that_does_not_resolve_is_two(self, tmp_path):
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(
                phabfive.create,
                "plan_spec",
                side_effect=PhabfiveDataException("No such user: 'nobody'"),
            ):
                result = invoke("--format=rich", "apply", "-f", write(tmp_path, CREATE))

        assert result.exit_code == 2
        assert "No such user" in result.stderr

    def test_an_instance_that_cannot_be_asked_is_three(self, tmp_path):
        """`get_app` says what went wrong and leaves with 1; this ladder says 3.

        The real `_instance` runs here - the autouse fixture is put back for
        this one test - because the mapping from `get_app`'s status to this
        command's is the thing under test.
        """
        from phabfive.cli import apps

        with patch.object(spec_run, "_instance", REAL_INSTANCE):
            with patch.object(
                apps,
                "new_app",
                side_effect=PhabfiveConnectionException("host is down"),
            ):
                result = invoke("--format=rich", "apply", "-f", write(tmp_path, CREATE))

        assert result.exit_code == 3
        assert "host is down" in result.stderr

    def test_a_run_that_stopped_partway_is_four(self, tmp_path):
        records = [
            a_record(),
            a_record(
                status="failed",
                path="tasks[1]",
                local_id=None,
                phid=None,
                id=None,
                monogram=None,
                title="Second",
                reason="Permission denied",
            ),
            a_record(
                status="skipped",
                path="tasks[2]",
                local_id=None,
                phid=None,
                id=None,
                monogram=None,
                title="Third",
            ),
        ]

        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(phabfive.create, "plan_spec", return_value=a_plan()):
                with patch.object(
                    phabfive.create, "apply_plan", return_value=iter(records)
                ):
                    result = invoke(
                        "--format=rich", "apply", "-f", write(tmp_path, CREATE)
                    )

        assert result.exit_code == 4
        assert "1 of 3 objects created" in result.stderr
        assert "Permission denied" in result.stdout

    def test_a_run_that_created_nothing_is_two(self, tmp_path):
        """Nothing exists, so there is nothing to clean up: not a partial run."""
        records = [
            a_record(
                status="failed",
                local_id=None,
                phid=None,
                id=None,
                monogram=None,
                reason="Permission denied",
            )
        ]

        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(phabfive.create, "plan_spec", return_value=a_plan()):
                with patch.object(
                    phabfive.create, "apply_plan", return_value=iter(records)
                ):
                    result = invoke(
                        "--format=rich", "apply", "-f", write(tmp_path, CREATE)
                    )

        assert result.exit_code == 2

    def test_a_bad_set_pair_is_one(self, tmp_path, never_builds_an_app):
        result = invoke(
            "--format=rich", "apply", "-f", write(tmp_path, CREATE), "--set", "nope"
        )

        assert result.exit_code == 1
        assert "NAME=VALUE" in result.stderr
        never_builds_an_app.assert_not_called()


class TestRecords:
    """What a program reads, in each machine format."""

    def test_json_answers_with_one_record_per_object(self, tmp_path):
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(phabfive.create, "plan_spec", return_value=a_plan()):
                with patch.object(
                    phabfive.create, "apply_plan", return_value=iter([a_record()])
                ):
                    result = invoke(
                        "--format=json", "apply", "-f", write(tmp_path, CREATE)
                    )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == [
            {
                "local_id": "first",
                "type": "task",
                "path": "tasks[0]",
                "status": "created",
                "phid": "PHID-TASK-first",
                "id": 123,
                "monogram": "T123",
                "title": "Set up CI",
                "reason": None,
            }
        ]
        assert "1 of 1 objects created." in result.stderr

    def test_jsonl_is_one_object_per_line(self, tmp_path):
        records = [
            a_record(),
            a_record(path="tasks[1]", local_id=None, monogram="T124"),
        ]

        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(phabfive.create, "plan_spec", return_value=a_plan()):
                with patch.object(
                    phabfive.create, "apply_plan", return_value=iter(records)
                ):
                    result = invoke(
                        "--format=jsonl", "apply", "-f", write(tmp_path, CREATE)
                    )

        assert result.exit_code == 0
        lines = [
            json.loads(line) for line in result.stdout.splitlines() if line.strip()
        ]
        assert [one["monogram"] for one in lines] == ["T123", "T124"]

    def test_a_dry_run_answers_with_the_plan(self, tmp_path):
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(phabfive.create, "plan_spec", return_value=a_plan()):
                result = invoke(
                    "--format=json",
                    "apply",
                    "-f",
                    write(tmp_path, CREATE),
                    "--dry-run",
                )

        assert result.exit_code == 0
        records = json.loads(result.stdout)
        assert [one["path"] for one in records] == ["tasks[0]"]
        assert records[0]["transactions"] == [{"type": "title", "value": "Set up CI"}]
        assert "would create 1 task" in result.stderr

    def test_an_anchor_is_shown_as_existing(self, tmp_path):
        """An anchor creates nothing, so the preview must not count it."""
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(
                phabfive.create, "plan_spec", return_value=a_plan(anchor=True)
            ):
                result = invoke(
                    "--format=rich",
                    "apply",
                    "-f",
                    write(tmp_path, CREATE),
                    "--dry-run",
                )

        assert result.exit_code == 0
        assert "(existing)" in result.stdout
        assert "would create 1 task" in result.stdout


class TestMonogramsStillWork:
    """A top-level verb must not eat the shortcut, or be eaten by it."""

    def test_a_monogram_still_expands(self):
        assert preprocess_monograms(["phabfive", "T123"]) == [
            "phabfive",
            "maniphest",
            "show",
            "T123",
        ]

    def test_apply_is_left_alone(self):
        argv = ["phabfive", "apply", "-f", "specs/create/sprint-tasks.yaml"]

        assert preprocess_monograms(list(argv)) == argv

    def test_search_is_left_alone(self):
        argv = ["phabfive", "search", "-f", "specs/search/blocked-tasks.yaml"]

        assert preprocess_monograms(list(argv)) == argv

    def test_the_filename_after_a_short_option_is_not_read_as_a_command(self):
        """`-f` takes the next argument, so nothing after it is a positional."""
        argv = ["phabfive", "--format=json", "apply", "-f", "T123.yaml"]

        assert preprocess_monograms(list(argv)) == argv

    def test_both_commands_are_registered(self):
        for name in ("apply", "search"):
            result = invoke(name, "--help")

            assert result.exit_code == 0, name
            assert "-f" in result.output
