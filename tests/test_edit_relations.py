# -*- coding: utf-8 -*-

"""``maniphest edit --parent/--unparent/--subtask/--unsubtask`` (#548).

Linking two tasks that both already exist. Every task is resolved before
anything is sent, since ``maniphest.edit`` accepts an edge to a PHID that
does not exist, and only a change is sent, as for commits and subscribers.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app
from phabfive.edit.plan import plan_task_edits
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveInputException,
    PhabfiveNotFoundException,
)
from phabfive.maniphest.core import Maniphest
from phabfive.retry import is_idempotent_edit

runner = CliRunner()

# id, PHID
TASKS = [
    (42, "PHID-TASK-42"),
    (43, "PHID-TASK-43"),
    (100, "PHID-TASK-epic"),
    (101, "PHID-TASK-other"),
    (200, "PHID-TASK-child"),
]

_EDGE_TYPES = {"parents": "task.parent", "subtasks": "task.subtask"}


def _phab(parents=(), subtasks=()):
    """A client that knows the tasks above, with `parents` and `subtasks`
    on every task it is asked about."""
    phab = MagicMock()

    def task_search(constraints, limit=None, **kwargs):
        return {
            "data": [
                _task(id_, phid)
                for id_, phid in TASKS
                if id_ in constraints.get("ids", ())
                or phid in constraints.get("phids", ())
            ]
        }

    def edge_search(sourcePHIDs, types, **kwargs):
        return {
            "data": [
                {"sourcePHID": source, "edgeType": edge_type, "destinationPHID": phid}
                for source in sourcePHIDs
                for edge_type, phids in (
                    ("task.parent", parents),
                    ("task.subtask", subtasks),
                )
                if edge_type in types
                for phid in phids
            ]
        }

    phab.maniphest.search.side_effect = task_search
    phab.edge.search.side_effect = edge_search
    phab.project.search.return_value = {"data": []}
    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}
    return maniphest


def _task(id_=42, phid="PHID-TASK-42"):
    return {
        "id": id_,
        "phid": phid,
        "fields": {
            "name": "A task",
            "status": {"name": "Open", "value": "open"},
            "priority": {"name": "High", "value": 80},
            "description": {"raw": ""},
            "ownerPHID": None,
        },
        "attachments": {
            "columns": {"boards": {}},
            "projects": {"projectPHIDs": []},
            "subscribers": {"subscriberPHIDs": []},
        },
    }


class TestBuild:
    def test_a_parent_is_added(self):
        transactions, changes = _maniphest(_phab()).build_task_edit(
            "42", _task(), parent=["T100"]
        )

        assert transactions == [{"type": "parents.add", "value": ["PHID-TASK-epic"]}]
        assert changes == [{"field": "Parents", "old": None, "new": "Added: T100"}]

    def test_a_subtask_is_added(self):
        transactions, changes = _maniphest(_phab()).build_task_edit(
            "42", _task(), subtask=["T200"]
        )

        assert transactions == [{"type": "subtasks.add", "value": ["PHID-TASK-child"]}]
        assert changes == [{"field": "Subtasks", "old": None, "new": "Added: T200"}]

    def test_a_phid_is_named_by_its_monogram(self):
        _, changes = _maniphest(_phab()).build_task_edit(
            "42", _task(), parent=["PHID-TASK-epic"]
        )

        assert changes == [{"field": "Parents", "old": None, "new": "Added: T100"}]

    def test_comma_separated_and_repeated(self):
        transactions, _ = _maniphest(_phab()).build_task_edit(
            "42", _task(), parent=["T100,T101", "T100"]
        )

        assert transactions == [
            {"type": "parents.add", "value": ["PHID-TASK-epic", "PHID-TASK-other"]}
        ]

    def test_one_already_a_parent_is_skipped(self):
        maniphest = _maniphest(_phab(parents=["PHID-TASK-epic"]))

        transactions, _ = maniphest.build_task_edit(
            "42", _task(), parent=["T100", "T101"]
        )

        assert transactions == [{"type": "parents.add", "value": ["PHID-TASK-other"]}]

    def test_all_there_already_is_no_change(self):
        maniphest = _maniphest(_phab(subtasks=["PHID-TASK-child"]))

        assert maniphest.build_task_edit("42", _task(), subtask=["T200"]) == ([], [])

    def test_a_parent_is_removed(self):
        maniphest = _maniphest(_phab(parents=["PHID-TASK-epic"]))

        transactions, changes = maniphest.build_task_edit(
            "42", _task(), unparent=["T100"]
        )

        assert transactions == [{"type": "parents.remove", "value": ["PHID-TASK-epic"]}]
        assert changes == [{"field": "Parents", "old": None, "new": "Removed: T100"}]

    def test_a_subtask_is_removed(self):
        maniphest = _maniphest(_phab(subtasks=["PHID-TASK-child"]))

        transactions, _ = maniphest.build_task_edit("42", _task(), unsubtask=["T200"])

        assert transactions == [
            {"type": "subtasks.remove", "value": ["PHID-TASK-child"]}
        ]

    def test_one_not_a_parent_is_not_removed(self):
        maniphest = _maniphest(_phab())

        assert maniphest.build_task_edit("42", _task(), unparent=["T100"]) == ([], [])

    def test_a_move_from_one_parent_to_another(self):
        maniphest = _maniphest(_phab(parents=["PHID-TASK-epic"]))

        transactions, _ = maniphest.build_task_edit(
            "42", _task(), parent=["T101"], unparent=["T100"]
        )

        assert transactions == [
            {"type": "parents.add", "value": ["PHID-TASK-other"]},
            {"type": "parents.remove", "value": ["PHID-TASK-epic"]},
        ]

    def test_parents_and_subtasks_in_one_edit(self):
        transactions, _ = _maniphest(_phab()).build_task_edit(
            "42", _task(), parent=["T100"], subtask=["T200"]
        )

        assert transactions == [
            {"type": "parents.add", "value": ["PHID-TASK-epic"]},
            {"type": "subtasks.add", "value": ["PHID-TASK-child"]},
        ]

    def test_one_task_both_added_and_removed_is_refused(self):
        """However each was spelled."""
        with pytest.raises(PhabfiveInputException, match="both add and remove T100"):
            _maniphest(_phab()).build_task_edit(
                "42", _task(), parent=["T100"], unparent=["PHID-TASK-epic"]
            )

    @pytest.mark.parametrize(
        "option, kind",
        [("parent", "parent"), ("unsubtask", "subtask")],
    )
    def test_the_task_itself_is_refused(self, option, kind):
        """The server would say "Graph cycle detected" and list PHIDs."""
        with pytest.raises(
            PhabfiveInputException, match=f"T42 cannot be its own {kind}: 't42'"
        ):
            _maniphest(_phab()).build_task_edit("42", _task(), **{option: ["t42"]})

    def test_one_task_as_both_parent_and_subtask_is_refused(self):
        with pytest.raises(
            PhabfiveInputException,
            match="Cannot add T100 as both a parent and a subtask of T42",
        ):
            _maniphest(_phab()).build_task_edit(
                "42", _task(), parent=["T100"], subtask=["PHID-TASK-epic"]
            )

    def test_an_unknown_task_names_the_option(self):
        with pytest.raises(
            PhabfiveNotFoundException, match="No such task for --unsubtask: 'T999'"
        ):
            _maniphest(_phab()).build_task_edit("42", _task(), unsubtask=["T999"])

    def test_something_else_is_not_a_task(self):
        with pytest.raises(PhabfiveInputException, match="Not a task for --parent"):
            _maniphest(_phab()).build_task_edit("42", _task(), parent=["#epics"])

    def test_a_failed_lookup_of_what_is_linked_is_not_no_change(self):
        """Removing must not be reported as no change when the edges are
        unknown."""
        phab = _phab()
        phab.edge.search.side_effect = PhabfiveAPIException("ERR-X", "boom")

        with pytest.raises(PhabfiveAPIException):
            _maniphest(phab).build_task_edit("42", _task(), unparent=["T100"])

    def test_only_the_relationship_edited_is_asked_for(self):
        phab = _phab()

        _maniphest(phab).build_task_edit("42", _task(), subtask=["T200"])

        [call] = phab.edge.search.call_args_list
        assert call.kwargs["types"] == ["task.subtask"]

    def test_without_relations_the_edges_are_not_asked_for(self):
        phab = _phab()

        _maniphest(phab).build_task_edit("42", _task(), title="Renamed")

        phab.edge.search.assert_not_called()


class TestPlan:
    def _plan(self, phab, **changes):
        maniphest = _maniphest(phab)
        task_data = {"42": _task(), "43": _task(43, "PHID-TASK-43")}
        return plan_task_edits(maniphest, ["42", "43"], task_data=task_data, **changes)

    def test_a_batch_resolves_the_tasks_once(self):
        phab = _phab()

        plan = self._plan(phab, parent=["T100"], unsubtask=["PHID-TASK-child"])

        assert [edit.transactions for edit in plan.edits] == [
            [{"type": "parents.add", "value": ["PHID-TASK-epic"]}],
            [{"type": "parents.add", "value": ["PHID-TASK-epic"]}],
        ]
        # One search for the monogram, one for the PHID, however many tasks
        assert phab.maniphest.search.call_count == 2

    def test_an_unknown_task_stops_the_batch_before_any_edit(self):
        phab = _phab()

        with pytest.raises(PhabfiveNotFoundException, match="for --parent"):
            self._plan(phab, parent=["T999"])

        phab.edge.search.assert_not_called()

    def test_one_task_both_added_and_removed_stops_the_batch(self):
        with pytest.raises(PhabfiveInputException, match="both add and remove"):
            self._plan(_phab(), subtask=["T200"], unsubtask=["T200"])

    def test_the_task_linked_to_itself_fails_that_task_alone(self):
        """`edit T42 T43 --parent=T43` still gives T42 its parent."""
        plan = self._plan(_phab(), parent=["T43"])

        assert [edit.task_id for edit in plan.edits] == ["42"]
        [failure] = plan.failures
        assert failure.task_id == "43"
        assert "cannot be its own parent" in str(failure.error)


class TestRetry:
    @pytest.mark.parametrize(
        "kind",
        ["parents.add", "parents.remove", "subtasks.add", "subtasks.remove"],
    )
    def test_an_edge_edit_is_idempotent(self, kind):
        assert is_idempotent_edit([{"type": kind, "value": ["PHID-TASK-epic"]}])


class TestCli:
    @pytest.mark.parametrize(
        "args",
        [["maniphest", "edit", "T1"], ["edit", "T1"]],
        ids=["maniphest-edit", "phabfive-edit"],
    )
    def test_the_options_are_passed_through(self, args):
        with (
            patch("phabfive.cli.maniphest._get_edit_app"),
            patch("phabfive.cli.edit._get_edit_app", create=True),
            patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
        ):
            result = runner.invoke(
                app,
                [
                    *args,
                    "--parent=T100",
                    "--parent=T101",
                    "--unparent=T99",
                    "--subtask=T200,T201",
                    "--unsubtask=T202",
                    "--yes",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = run_edit.call_args.kwargs
        assert kwargs["parent"] == ["T100", "T101"]
        assert kwargs["unparent"] == ["T99"]
        assert kwargs["subtask"] == ["T200,T201"]
        assert kwargs["unsubtask"] == ["T202"]

    def test_a_relation_alone_is_an_edit_not_a_description(self):
        """Any option means the description is not opened in $EDITOR."""
        from phabfive.cli import edit_flow

        edit = MagicMock()
        edit.parse_object_ids.return_value = [("task", "42")]

        with patch.object(edit_flow, "_edit_task_single", return_value=0) as single:
            assert edit_flow.run_edit(edit, object_id="T42", unsubtask=["T200"]) == 0

        assert single.call_args.kwargs["edit_description_in_editor"] is False
        assert single.call_args.kwargs["unsubtask"] == ["T200"]
