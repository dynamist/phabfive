# -*- coding: utf-8 -*-
"""Edit.plan and Edit.apply: an edit as data, then an edit made.

The command reviews, confirms and previews between the two; these tests hold
the library to doing neither - no output, no prompt, nothing sent by plan().
"""

from unittest import mock

import pytest

from phabfive.edit import Edit
from phabfive.edit.plan import EditFailure, EditPlan, TaskEdit
from phabfive.exceptions import (
    PhabfiveDataException,
    PhabfiveInputException,
    PhabfiveValidationException,
)

URL = "https://phorge.example.com/api/"
TOKEN = "api-" + "a" * 28


def _task(name="current", description="current", boards=None, policy=None):
    return {
        "fields": {
            "name": name,
            "description": {"raw": description},
            "policy": policy or {},
        },
        "attachments": {"columns": {"boards": boards or {}}},
    }


@pytest.fixture
def edit():
    with mock.patch("phabfive.core.Phabricator"):
        app = Edit(url=URL, token=TOKEN)
    app.maniphest = mock.MagicMock()
    app.maniphest._get_task_data.side_effect = lambda task_id: _task()
    app.maniphest.build_task_edit.side_effect = lambda task_id, data, **kw: (
        [{"type": "status", "value": "resolved"}],
        [{"field": "Status", "old": "Open", "new": "Resolved"}],
    )
    return app


class TestPlan:
    def test_one_task_edit_per_task_in_order(self, edit):
        plan = edit.plan("T2,T1", status="resolved")

        assert isinstance(plan, EditPlan)
        assert [entry.task_id for entry in plan.entries] == ["2", "1"]
        assert all(isinstance(entry, TaskEdit) for entry in plan.entries)
        assert plan.edits[0].changes == [
            {"field": "Status", "old": "Open", "new": "Resolved"}
        ]

    def test_planning_sends_nothing(self, edit):
        edit.plan("T1", status="resolved")

        edit.maniphest.apply_task_edit.assert_not_called()

    def test_planning_prints_nothing(self, edit, capsys):
        edit.plan("T1,T2", status="resolved")

        assert capsys.readouterr() == ("", "")

    @pytest.mark.parametrize("ids", ["T1", ["T1"], [1], ["1"], ("t1",)])
    def test_ids_in_any_reasonable_shape(self, edit, ids):
        assert [entry.task_id for entry in edit.plan(ids, status="x").entries] == ["1"]

    def test_only_tasks(self, edit):
        with pytest.raises(PhabfiveInputException, match="Only tasks"):
            edit.plan(["P1"], status="resolved")

    def test_a_task_already_as_asked_is_a_noop(self, edit):
        edit.maniphest.build_task_edit.side_effect = None
        edit.maniphest.build_task_edit.return_value = ([], [])

        [task_edit] = edit.plan("T1", status="open").entries

        assert task_edit.noop

    def test_a_task_that_cannot_be_planned_is_a_failure_not_an_abort(self, edit):
        refused = PhabfiveDataException("no such status")

        def build(task_id, data, **kw):
            if task_id == "1":
                raise refused
            return ([{"type": "status"}], [{"field": "Status"}])

        edit.maniphest.build_task_edit.side_effect = build

        plan = edit.plan("T1,T2", status="bogus")

        assert plan.failures == [EditFailure("1", refused)]
        assert [task_edit.task_id for task_edit in plan.edits] == ["2"]


class TestValidation:
    def test_every_failure_is_reported_and_nothing_is_planned(self, edit):
        edit.maniphest._get_task_data.side_effect = LookupError("gone")

        with pytest.raises(PhabfiveValidationException) as caught:
            edit.plan("T1,T2", status="resolved")

        assert [p.monogram for p in caught.value.problems] == ["T1", "T2"]
        assert str(caught.value) == (
            "Validation failed for 2 task(s):\n  - T1: gone\n  - T2: gone"
        )
        edit.maniphest.build_task_edit.assert_not_called()

    def test_several_boards_name_them_for_a_suggestion(self, edit):
        edit.maniphest._get_task_data.side_effect = lambda task_id: _task(
            boards={"PHID-PROJ-a": {}, "PHID-PROJ-b": {}}
        )
        edit.maniphest.phab.project.search.return_value = {
            "data": [{"fields": {"name": "Alpha"}}, {"fields": {"name": "Beta"}}]
        }

        with pytest.raises(PhabfiveValidationException) as caught:
            edit.plan("T1,T2", column="Done")

        assert caught.value.errors_by_boards == {
            frozenset({"Alpha", "Beta"}): ["1", "2"]
        }

    def test_a_policy_typo_is_refused_before_any_task_is_fetched(self, edit):
        from phabfive.exceptions import PhabfiveConfigException

        with pytest.raises(PhabfiveConfigException):
            edit.plan("T1", visible_to="publik")

        edit.maniphest._get_task_data.assert_not_called()


class TestNeedsConfirmation:
    def test_a_structured_field_does_not(self, edit):
        assert not edit.plan("T1", status="resolved").needs_confirmation

    def test_a_new_title_does(self, edit):
        assert edit.plan("T1", title="new").needs_confirmation

    def test_the_same_title_does_not(self, edit):
        assert not edit.plan("T1", title="current").needs_confirmation

    def test_worked_out_only_when_asked(self, edit):
        """Comparing a policy costs a lookup, so planning alone must not."""
        with mock.patch(
            "phabfive.edit.plan._needs_policy_confirmation", return_value=True
        ) as needs:
            plan = edit.plan("T1", visible_to="public")
            needs.assert_not_called()

            assert plan.needs_confirmation
            assert plan.needs_confirmation

        needs.assert_called_once()


class TestApply:
    def test_sends_exactly_what_was_planned(self, edit):
        [task_edit] = edit.plan("T1", status="resolved").entries

        result = edit.apply(task_edit)

        edit.maniphest.apply_task_edit.assert_called_once_with(
            "1", [{"type": "status", "value": "resolved"}]
        )
        assert result == {"task_id": "1", "changes": task_edit.changes}

    def test_a_noop_sends_nothing(self, edit):
        edit.apply(TaskEdit("1", [], []))

        edit.maniphest.apply_task_edit.assert_not_called()

    def test_apply_all_skips_failures_and_stops_at_a_refusal(self, edit):
        plan = EditPlan(
            [
                TaskEdit("1", [{"type": "a"}], []),
                EditFailure("2", PhabfiveDataException("x")),
                TaskEdit("3", [{"type": "b"}], []),
                TaskEdit("4", [{"type": "c"}], []),
            ]
        )
        edit.maniphest.apply_task_edit.side_effect = [
            None,
            PhabfiveDataException("refused"),
        ]

        with pytest.raises(PhabfiveDataException, match="refused"):
            edit.apply_all(plan)

        assert [c.args[0] for c in edit.maniphest.apply_task_edit.call_args_list] == [
            "1",
            "3",
        ]


def test_edit_task_by_id_dry_run_prints_nothing(capsys):
    """The preview is the caller's to render; the method only returns it."""
    from phabfive.maniphest import Maniphest

    with mock.patch("phabfive.core.Phabricator"):
        maniphest = Maniphest(url=URL, token=TOKEN)
    changes = [{"field": "Status", "old": "Open", "new": "Resolved"}]
    with mock.patch.object(
        Maniphest, "build_task_edit", return_value=([{"type": "status"}], changes)
    ):
        result = maniphest.edit_task_by_id("1", status="resolved", dry_run=True)

    assert result == {"task_id": "1", "changes": changes, "dry_run": True}
    assert capsys.readouterr() == ("", "")
