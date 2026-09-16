# -*- coding: utf-8 -*-

"""Asking for an object that does not exist must fail the command.

`maniphest show T0` used to log "Task T0 not found" and still exit 0, so
`phabfive maniphest show "$id" || handle_error` silently did nothing.
`paste show P0` exited 1 but by way of an unhandled traceback, and
`diffusion uri list nosuchrepo` exited 0 printing nothing at all.

`passphrase show K999` was already correct and is the reference these
tests hold the other commands to.
"""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from phabfive.cli.diffusion import diffusion_app
from phabfive.cli.maniphest import maniphest_app
from phabfive.cli.paste import paste_app

runner = CliRunner()


def _output(result):
    """Combined stdout/stderr regardless of click version."""
    output = result.output
    try:
        output += result.stderr
    except (ValueError, AttributeError):
        pass
    return output


class TestManiphestShowExitCode:
    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_missing_task_exits_non_zero(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_show.return_value = None
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["show", "T0"])

        assert result.exit_code == 1

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_existing_task_exits_zero(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_show.return_value = {
            "tasks": [{"Task": {"Name": "a task"}}],
            "project_names": {},
            "missing_ids": [],
        }
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["show", "T1"])

        assert result.exit_code == 0

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_partial_result_exits_non_zero(self, mock_get_app):
        """Found some, missed others: still a failed lookup."""
        mock_m = MagicMock()
        mock_m.task_show.return_value = {
            "tasks": [{"Task": {"Name": "a task"}}],
            "project_names": {},
            "missing_ids": [999],
        }
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["show", "T1", "T999"])

        assert result.exit_code == 1


class TestPasteShowExitCode:
    @patch("phabfive.cli.paste._get_paste_app")
    def test_missing_paste_exits_non_zero_without_traceback(self, mock_get_app):
        mock_p = MagicMock()
        mock_p.paste_show.return_value = None
        mock_get_app.return_value = mock_p

        result = runner.invoke(paste_app, ["show", "P0"])

        assert result.exit_code == 1
        assert "Traceback" not in _output(result)
        assert "PhabfiveDataException" not in _output(result)

    @patch("phabfive.cli.paste._get_paste_app")
    def test_existing_paste_exits_zero(self, mock_get_app):
        mock_p = MagicMock()
        mock_p.paste_show.return_value = {
            "pastes": [{"id": "P1", "title": "a paste"}],
            "missing_ids": [],
        }
        mock_get_app.return_value = mock_p

        result = runner.invoke(paste_app, ["show", "P1"])

        assert result.exit_code == 0

    @patch("phabfive.cli.paste._get_paste_app")
    def test_partial_result_exits_non_zero(self, mock_get_app):
        mock_p = MagicMock()
        mock_p.paste_show.return_value = {
            "pastes": [{"id": "P1", "title": "a paste"}],
            "missing_ids": [999],
        }
        mock_get_app.return_value = mock_p

        result = runner.invoke(paste_app, ["show", "P1", "P999"])

        assert result.exit_code == 1


class TestDiffusionUriListExitCode:
    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_unknown_repository_exits_non_zero(self, mock_get_app):
        mock_d = MagicMock()
        mock_d.get_uris_formatted.return_value = None
        mock_get_app.return_value = mock_d

        result = runner.invoke(diffusion_app, ["uri", "list", "nosuchrepo"])

        assert result.exit_code == 1
        assert "not found" in _output(result)

    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_repository_without_uris_exits_zero(self, mock_get_app):
        """An empty result is not a failure."""
        mock_d = MagicMock()
        mock_d.get_uris_formatted.return_value = []
        mock_get_app.return_value = mock_d

        result = runner.invoke(diffusion_app, ["uri", "list", "myrepo"])

        assert result.exit_code == 0

    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_repository_with_uris_exits_zero(self, mock_get_app):
        mock_d = MagicMock()
        mock_d.get_uris_formatted.return_value = ["git@example.com:org/myrepo.git"]
        mock_get_app.return_value = mock_d

        result = runner.invoke(diffusion_app, ["uri", "list", "myrepo"])

        assert result.exit_code == 0
        assert "git@example.com:org/myrepo.git" in _output(result)
