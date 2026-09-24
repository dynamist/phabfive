# -*- coding: utf-8 -*-

"""`--unassign` removes a task's assignee, from `maniphest edit` and
`phabfive edit` alike."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app as main_app
from phabfive.cli.maniphest import maniphest_app
from phabfive.exceptions import PhabfiveInputException
from phabfive.maniphest.core import Maniphest

runner = CliRunner()


def _maniphest():
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.phab.user.search.return_value = {
        "data": [{"phid": "PHID-USER-alice", "fields": {"username": "alice"}}]
    }
    maniphest.url = "http://phorge.localhost"
    return maniphest


def _task(owner):
    return {
        "id": 42,
        "phid": "PHID-TASK-42",
        "fields": {
            "name": "A task",
            "status": {"name": "Open", "value": "open"},
            "priority": {"name": "High", "value": 80},
            "description": {"raw": ""},
            "ownerPHID": owner,
        },
        "attachments": {
            "columns": {"boards": {}},
            "projects": {"projectPHIDs": []},
            "subscribers": {"subscriberPHIDs": []},
        },
    }


class TestBuildTaskEdit:
    def test_a_null_owner_clears_the_assignee(self):
        transactions, changes = _maniphest().build_task_edit(
            "42", _task("PHID-USER-alice"), unassign=True
        )

        assert transactions == [{"type": "owner", "value": None}]
        assert changes == [{"field": "Assignee", "old": "alice", "new": "(none)"}]

    def test_an_unassigned_task_changes_nothing(self):
        transactions, changes = _maniphest().build_task_edit(
            "42", _task(None), unassign=True
        )

        assert transactions == []
        assert changes == []

    def test_assign_and_unassign_together_is_refused(self):
        maniphest = _maniphest()

        with pytest.raises(PhabfiveInputException, match="--unassign"):
            maniphest.build_task_edit("42", assign="alice", unassign=True)

        # Refused before the task is fetched
        maniphest.phab.maniphest.search.assert_not_called()


@pytest.fixture
def mock_config(monkeypatch):
    """Configuration and a client that need no network."""
    monkeypatch.setenv("PHAB_TOKEN", "cli-testtoken1234567890123456789")
    monkeypatch.setenv("PHAB_URL", "https://phabricator.example.com/api/")
    monkeypatch.setattr("phabfive.core.Phabricator", lambda **kwargs: MagicMock())


class TestRunEdit:
    def test_unassign_alone_does_not_open_the_editor(self, mock_config):
        from phabfive.cli.edit_flow import run_edit
        from phabfive.edit import Edit

        with patch("phabfive.cli.edit_flow._edit_task_single", return_value=0) as one:
            run_edit(Edit(), object_id="T123", unassign=True)

        assert one.call_args.kwargs["unassign"] is True
        assert one.call_args.kwargs["edit_description_in_editor"] is False

    def test_it_reaches_the_batch_path(self, mock_config):
        from phabfive.cli.edit_flow import run_edit
        from phabfive.edit import Edit

        with patch("phabfive.cli.edit_flow.edit_tasks_batch", return_value=0) as batch:
            run_edit(Edit(), object_id="T123,T124", unassign=True)

        assert batch.call_args.kwargs["unassign"] is True

    def test_assign_and_unassign_is_refused_before_any_task(self, mock_config, capsys):
        from phabfive.cli.edit_flow import run_edit
        from phabfive.edit import Edit

        with patch("phabfive.cli.edit_flow._edit_task_single") as one:
            status = run_edit(Edit(), object_id="T123", assign="alice", unassign=True)

        assert status == 1
        one.assert_not_called()
        assert "--assign and --unassign" in capsys.readouterr().err


class TestCommands:
    @pytest.mark.parametrize(
        "app, args, getter",
        [
            (maniphest_app, ["edit", "T1"], "phabfive.cli.maniphest._get_edit_app"),
            (main_app, ["edit", "T1"], "phabfive.cli.edit._get_edit_app"),
        ],
    )
    def test_the_flag_reaches_run_edit(self, app, args, getter):
        with (
            patch(getter),
            patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
        ):
            result = runner.invoke(app, [*args, "--unassign"])

        assert result.exit_code == 0, result.output
        assert run_edit.call_args.kwargs["unassign"] is True
