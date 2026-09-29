# -*- coding: utf-8 -*-

"""``maniphest create --parent``: creating a task as a subtask (#545).

A parent is named by monogram or PHID, resolved before anything is written,
and linked with ``parents.add`` in the request that creates the task, so the
parent itself is never edited.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app
from phabfive.exceptions import PhabfiveInputException, PhabfiveNotFoundException
from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.resolvers import resolve_task_phids

runner = CliRunner()

# id, PHID
TASKS = [(123, "PHID-TASK-parent"), (124, "PHID-TASK-other")]


def _phab():
    """A client that knows the tasks above."""
    phab = MagicMock()

    def task_search(constraints, limit=None):
        return {
            "data": [
                {"id": id_, "phid": phid}
                for id_, phid in TASKS
                if id_ in constraints.get("ids", ())
                or phid in constraints.get("phids", ())
            ]
        }

    phab.maniphest.search.side_effect = task_search
    phab.maniphest.edit.return_value = {"object": {"id": 1, "phid": "PHID-TASK-1"}}
    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}
    return maniphest


class TestResolveTaskPhids:
    def test_a_monogram_and_a_phid_name_their_tasks(self):
        assert resolve_task_phids(_phab(), ["T123", "PHID-TASK-other"]) == {
            "T123": ("PHID-TASK-parent", "T123"),
            "PHID-TASK-other": ("PHID-TASK-other", "T124"),
        }

    def test_a_lowercase_monogram_is_accepted(self):
        assert resolve_task_phids(_phab(), ["t123"]) == {
            "t123": ("PHID-TASK-parent", "T123")
        }

    def test_one_search_per_kind_however_many_values(self):
        phab = _phab()

        resolve_task_phids(phab, ["T123", "T124", "T123", "PHID-TASK-other"])

        assert phab.maniphest.search.call_count == 2

    def test_every_unknown_one_is_named(self):
        with pytest.raises(PhabfiveNotFoundException) as excinfo:
            resolve_task_phids(
                _phab(), ["T999", "T123", "PHID-TASK-nope"], option="--parent"
            )

        assert str(excinfo.value) == (
            "No such task for --parent: 'T999', 'PHID-TASK-nope'"
        )

    @pytest.mark.parametrize("value", ["123", "P123", "PHID-PROJ-abc", "@none"])
    def test_what_is_not_a_task_is_refused_without_asking(self, value):
        phab = _phab()

        with pytest.raises(PhabfiveInputException, match="Not a task for --parent"):
            resolve_task_phids(phab, [value], option="--parent")

        phab.maniphest.search.assert_not_called()


class TestCreate:
    def test_parents_are_added_in_the_create(self):
        phab = _phab()

        _maniphest(phab).create_task("A subtask", parents=["T123", "PHID-TASK-other"])

        [call] = phab.maniphest.edit.call_args_list
        assert "objectIdentifier" not in call.kwargs
        assert {
            "type": "parents.add",
            "value": ["PHID-TASK-parent", "PHID-TASK-other"],
        } in call.kwargs["transactions"]

    def test_one_parent_spelled_twice_is_added_once(self):
        phab = _phab()

        _maniphest(phab).create_task("A subtask", parents=["T123,PHID-TASK-parent"])

        transactions = phab.maniphest.edit.call_args.kwargs["transactions"]
        assert {"type": "parents.add", "value": ["PHID-TASK-parent"]} in transactions

    def test_without_parents_none_are_sent_or_looked_up(self):
        phab = _phab()

        _maniphest(phab).create_task("A task")

        transactions = phab.maniphest.edit.call_args.kwargs["transactions"]
        assert not [t for t in transactions if t["type"] == "parents.add"]
        phab.maniphest.search.assert_not_called()

    def test_a_dry_run_names_them(self):
        phab = _phab()

        result = _maniphest(phab).create_task(
            "A subtask", parents=["PHID-TASK-parent,T124"], dry_run=True
        )

        assert result["parents"] == ["T123", "T124"]
        phab.maniphest.edit.assert_not_called()

    def test_an_unknown_parent_creates_nothing(self):
        phab = _phab()

        with pytest.raises(PhabfiveNotFoundException):
            _maniphest(phab).create_task("A subtask", parents=["T999"])

        phab.maniphest.edit.assert_not_called()


class TestCli:
    def _invoke(self, instance, *args):
        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=instance):
            return runner.invoke(
                app, ["maniphest", "create", "A subtask", "--description=x", *args]
            )

    def test_parent_is_passed_through(self):
        instance = MagicMock()
        instance.create_task.return_value = {"id": 1, "uri": "http://x/T1"}

        result = self._invoke(instance, "--parent=T123,T124", "--parent", "T125")

        assert result.exit_code == 0, result.output
        assert instance.create_task.call_args.kwargs["parents"] == [
            "T123",
            "T124",
            "T125",
        ]

    def test_a_dry_run_names_them(self):
        instance = MagicMock()
        instance.create_task.return_value = {
            "dry_run": True,
            "title": "A subtask",
            "parents": ["T123"],
        }

        result = self._invoke(instance, "--parent=T123", "--dry-run")

        assert result.exit_code == 0, result.output
        assert "Parents: T123" in result.output

    def test_an_unknown_parent_is_one_line_and_exit_1(self):
        instance = MagicMock()
        instance.create_task.side_effect = PhabfiveNotFoundException(
            "No such task for --parent: 'T999'"
        )

        result = self._invoke(instance, "--parent=T999")

        assert result.exit_code == 1
        assert "Error: No such task for --parent: 'T999'" in result.output

    def test_help_offers_it(self):
        result = runner.invoke(app, ["maniphest", "create", "--help"])

        assert "--parent" in result.output
