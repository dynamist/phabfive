# -*- coding: utf-8 -*-
"""`phabfive apply -f` with an edit spec (`kind: edit`).

`apply` runs both kinds that write. What is pinned here is the edit half of
the ladder `tests/test_cli_apply.py` pins for a create: the kind is
dispatched on, nothing is written until both layers pass, and the exit
status says which layer refused and whether anything changed.

Two things differ from a create and are the reason this file exists. An
object that already matches is `unchanged` and the run still leaves with 0,
which is what makes applying a document twice safe. And a problem with any
item refuses the whole document: half of it applied is a state nobody wrote
down.
"""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app, spec_run
from phabfive.core import Phabfive
from phabfive.edit.spec import EditItem, EditPlanError, EditRecord, EditSpecPlan
from phabfive.exceptions import PhabfiveConnectionException
from phabfive.spec.problems import Layer, Problem

runner = CliRunner()

EDIT = """\
spec: phorge/v1alpha1
kind: edit
tasks:
  - task: T1
    status: resolved
  - task: T2
    status: resolved
"""

CREATE = """\
spec: phorge/v1alpha1
kind: create
tasks:
  - title: Set up CI
"""

CHANGE = ({"field": "Status", "old": "Open", "new": "Resolved"},)


@pytest.fixture(autouse=True)
def restore_output_format():
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


@pytest.fixture
def instance():
    """An app for the command to hold; the planner is what is stood in for."""
    marker = object()

    with patch.object(spec_run, "_instance", return_value=marker):
        yield marker


@pytest.fixture(autouse=True)
def never_builds_an_app(request):
    if "instance" in request.fixturenames:
        yield
        return

    with patch.object(
        spec_run,
        "_instance",
        side_effect=AssertionError("the command constructed an app"),
    ):
        yield


def write(tmp_path, text, name="spec.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def invoke(*args, **kwargs):
    return runner.invoke(app, list(args), **kwargs)


def a_plan():
    return EditSpecPlan(
        items=(
            EditItem("task", "tasks[0]", "T1", "First", ({"type": "status"},), CHANGE),
            EditItem("task", "tasks[1]", "T2", "Second"),
        )
    )


def stale():
    return Problem(
        object="tasks[0]",
        field="modified",
        value="2026-01-31T09:30:00Z",
        reason="T1 was modified after this item was written",
        code="stale",
        layer=Layer.ONLINE,
    )


def planning(plan):
    return patch("phabfive.edit.spec.plan_edit", return_value=plan)


def applying(*records):
    return patch("phabfive.edit.spec.apply_edit_plan", return_value=iter(records))


def record(status, monogram="T1", **more):
    return EditRecord(
        object_type="task",
        path="tasks[0]",
        status=status,
        monogram=monogram,
        title="First",
        **more,
    )


class TestDispatch:
    def test_apply_runs_an_edit_spec(self, tmp_path, instance):
        with planning(a_plan()) as plan, applying():
            result = invoke("apply", "-f", write(tmp_path, EDIT))

        assert result.exit_code == 0, result.output
        plan.assert_called_once()

    def test_search_refuses_it_and_names_apply(self, tmp_path):
        path = write(tmp_path, EDIT)
        result = invoke("search", "-f", path)

        assert result.exit_code == 1
        assert f"{path} is an edit spec" in result.output
        assert f"phabfive apply -f {path}" in result.output

    def test_an_edit_that_lost_its_kind_never_reaches_the_instance(self, tmp_path):
        """Read as a create, and refused offline for the key no create has."""
        text = EDIT.replace("kind: edit\n", "")
        result = invoke("--format=json", "apply", "-f", write(tmp_path, text))

        assert result.exit_code == 1
        assert "kind: edit" in result.output

    def test_the_deprecated_create_with_does_not_run_it(self, tmp_path):
        """`--with` shares the dispatch, and must not become a way to edit.

        `maniphest create --with` is a create command. An edit spec handed
        to it is refused by the create planner's own kind guard before any
        task is read, let alone written.
        """
        applied = []

        with (
            patch("phabfive.cli.apps.get_app", return_value=object()),
            patch(
                "phabfive.edit.spec.apply_edit_plan",
                side_effect=lambda *args, **kwargs: applied.append(args),
            ),
        ):
            result = invoke("maniphest", "create", "--with", write(tmp_path, EDIT))

        assert result.exit_code != 0
        assert applied == []

    def test_spec_validate_takes_the_kind(self, tmp_path):
        result = invoke(
            "spec", "validate", "--offline", "--kind", "edit", write(tmp_path, EDIT)
        )

        assert result.exit_code == 0, result.output


class TestNothingIsWrittenUntilBothLayersPass:
    def test_an_offline_error_is_1(self, tmp_path):
        text = EDIT.replace("status: resolved", "priority: raise", 1)
        result = invoke("--format=json", "apply", "-f", write(tmp_path, text))

        assert result.exit_code == 1
        assert [one["code"] for one in json.loads(result.stdout)] == ["relative-value"]

    def test_a_problem_with_any_item_is_2_and_applies_nothing(self, tmp_path, instance):
        plan = EditSpecPlan(items=a_plan().items[1:], problems=(stale(),))

        with planning(plan), applying() as apply:
            result = invoke("--format=json", "apply", "-f", write(tmp_path, EDIT))

        assert result.exit_code == 2
        assert [one["code"] for one in json.loads(result.stdout)] == ["stale"]
        apply.assert_not_called()

    def test_a_plan_the_planner_refuses_is_1(self, tmp_path, instance):
        with patch("phabfive.edit.spec.plan_edit", side_effect=EditPlanError("no")):
            result = invoke("apply", "-f", write(tmp_path, EDIT))

        assert result.exit_code == 1

    def test_an_instance_that_cannot_be_asked_is_3(self, tmp_path, instance):
        with patch(
            "phabfive.edit.spec.plan_edit",
            side_effect=PhabfiveConnectionException("no route"),
        ):
            result = invoke("apply", "-f", write(tmp_path, EDIT))

        assert result.exit_code == 3
        assert "could not be asked" in result.output


class TestDryRun:
    def test_it_prints_the_changes_and_writes_nothing(self, tmp_path, instance):
        with planning(a_plan()), applying() as apply:
            result = invoke(
                "--format=rich", "apply", "-f", write(tmp_path, EDIT), "--dry-run"
            )

        assert result.exit_code == 0
        assert "would edit 1 object, 1 already as written" in result.output
        assert "- task T1 'First'" in result.output
        assert "Status: Open -> Resolved" in result.output
        assert "(unchanged) task T2 'Second'" in result.output
        apply.assert_not_called()

    def test_a_machine_format_gets_the_plans_records(self, tmp_path, instance):
        with planning(a_plan()), applying():
            result = invoke(
                "--format=json", "apply", "-f", write(tmp_path, EDIT), "--dry-run"
            )

        assert [one["status"] for one in json.loads(result.stdout)] == [
            "would-edit",
            "unchanged",
        ]


class TestApplying:
    def test_edited_and_unchanged_are_both_a_clean_run(self, tmp_path, instance):
        with (
            planning(a_plan()),
            applying(record("edited", changes=CHANGE), record("unchanged", "T2")),
        ):
            result = invoke("--format=json", "apply", "-f", write(tmp_path, EDIT))

        assert result.exit_code == 0
        assert [one["status"] for one in json.loads(result.stdout)] == [
            "edited",
            "unchanged",
        ]

    def test_nothing_to_change_is_still_0(self, tmp_path, instance):
        with planning(a_plan()), applying(record("unchanged")):
            result = invoke("apply", "-f", write(tmp_path, EDIT))

        assert result.exit_code == 0

    def test_a_run_that_stopped_partway_is_4(self, tmp_path, instance):
        with (
            planning(a_plan()),
            applying(
                record("edited", changes=CHANGE),
                record("failed", "T2", reason="refused"),
            ),
        ):
            result = invoke("apply", "-f", write(tmp_path, EDIT))

        assert result.exit_code == 4
        assert "refused" in result.output

    def test_a_run_refused_before_anything_changed_is_2(self, tmp_path, instance):
        with planning(a_plan()), applying(record("failed", reason="refused")):
            result = invoke("apply", "-f", write(tmp_path, EDIT))

        assert result.exit_code == 2

    def test_jsonl_is_one_record_per_line_as_it_happens(self, tmp_path, instance):
        with (
            planning(a_plan()),
            applying(record("edited", changes=CHANGE), record("unchanged", "T2")),
        ):
            result = invoke("--format=jsonl", "apply", "-f", write(tmp_path, EDIT))

        lines = [json.loads(line) for line in result.stdout.splitlines() if line]

        assert [one["status"] for one in lines] == ["edited", "unchanged"]


class TestForce:
    def test_it_reaches_the_planner(self, tmp_path, instance):
        with planning(a_plan()) as plan, applying():
            invoke("apply", "-f", write(tmp_path, EDIT), "--force")

        assert plan.call_args.kwargs == {"force": True}

    def test_without_it_the_planner_checks_the_stamp(self, tmp_path, instance):
        with planning(a_plan()) as plan, applying():
            invoke("apply", "-f", write(tmp_path, EDIT))

        assert plan.call_args.kwargs == {"force": False}

    def test_a_create_spec_has_nothing_to_overwrite(self, tmp_path):
        result = invoke("apply", "-f", write(tmp_path, CREATE), "--force")

        assert result.exit_code == 1
        assert "--force is for an edit spec" in result.output


class TestStandardInput:
    def test_a_dash_reads_the_spec_from_stdin(self, instance):
        with planning(a_plan()) as plan, applying():
            result = invoke("apply", "-f", "-", "--dry-run", input=EDIT)

        assert result.exit_code == 0, result.output
        [(_, spec), _] = plan.call_args

        assert spec.kind.value == "edit"
        assert [item["task"] for item in spec.items("task")] == ["T1", "T2"]

    def test_json_on_stdin_is_told_from_its_content(self, instance):
        document = json.dumps({"kind": "edit", "tasks": [{"task": "T1"}]})

        with planning(a_plan()) as plan, applying():
            result = invoke("apply", "-f", "-", "--dry-run", input=document)

        assert result.exit_code == 0, result.output
        assert plan.call_args.args[1].items("task") == [{"task": "T1"}]

    def test_spec_validate_reads_it_too(self):
        result = invoke("spec", "validate", "--offline", "-", input=EDIT)

        assert result.exit_code == 0, result.output

    def test_nothing_on_stdin_is_an_unreadable_spec(self):
        result = invoke("apply", "-f", "-", input="")

        assert result.exit_code == 1
