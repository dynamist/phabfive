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

import pytest

from typer.testing import CliRunner

from phabfive.cli.diffusion import diffusion_app
from phabfive.cli.maniphest import maniphest_app
from phabfive.cli.paste import paste_app
from phabfive.exceptions import PhabfiveDataException

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
        mock_d.uri_list.side_effect = PhabfiveDataException(
            "Repository 'nosuchrepo' not found"
        )
        mock_get_app.return_value = mock_d

        result = runner.invoke(diffusion_app, ["uri", "list", "nosuchrepo"])

        assert result.exit_code == 1
        assert "not found" in _output(result)

    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_repository_without_uris_exits_zero(self, mock_get_app):
        """An empty result is not a failure."""
        mock_d = MagicMock()
        mock_d.uri_list.return_value = {"uris": [], "total": 0}
        mock_get_app.return_value = mock_d

        result = runner.invoke(diffusion_app, ["uri", "list", "myrepo"])

        assert result.exit_code == 0

    @pytest.mark.parametrize("output_format", ["json", "jsonl", "yaml", "rich"])
    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_repository_without_uris_says_so_on_stderr(
        self, mock_get_app, output_format
    ):
        """Not silence: an empty list says why, and stdout stays parseable (#395)."""
        mock_d = MagicMock()
        mock_d.uri_list.return_value = {"uris": [], "total": 0}
        mock_get_app.return_value = mock_d

        with patch(
            "phabfive.cli.diffusion._get_output_format", return_value=output_format
        ):
            result = runner.invoke(diffusion_app, ["uri", "list", "R1"])

        assert result.exit_code == 0
        assert result.stderr == "No URIs found: R1 has none\n"
        assert "No URIs" not in result.stdout

    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_filtered_away_uris_say_so_on_stderr(self, mock_get_app):
        mock_d = MagicMock()
        mock_d.uri_list.return_value = {"uris": [], "total": 2}
        mock_get_app.return_value = mock_d

        with patch("phabfive.cli.diffusion._get_output_format", return_value="json"):
            result = runner.invoke(diffusion_app, ["uri", "list", "R3", "--builtin"])

        assert result.exit_code == 0
        assert result.stderr == (
            "No URIs found: R3 has 2, none matching the filters given\n"
        )
        assert result.stdout == "[]\n"

    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_repository_with_uris_exits_zero(self, mock_get_app):
        mock_d = MagicMock()
        mock_d.uri_list.return_value = {
            "uris": [{"URI": "git@example.com:org/myrepo.git"}]
        }
        mock_get_app.return_value = mock_d

        result = runner.invoke(diffusion_app, ["uri", "list", "myrepo"])

        assert result.exit_code == 0
        assert "git@example.com:org/myrepo.git" in _output(result)


class TestDiffusionRepoListEmpty:
    """`repo list` that lists nothing says why on stderr and exits 0 (#395)."""

    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_instance_without_repositories(self, mock_get_app):
        mock_d = MagicMock()
        mock_d.repo_list.return_value = {"repositories": [], "total": 0}
        mock_get_app.return_value = mock_d

        with patch("phabfive.cli.diffusion._get_output_format", return_value="json"):
            result = runner.invoke(diffusion_app, ["repo", "list"])

        assert result.exit_code == 0
        assert result.stderr == "No repositories found\n"
        assert result.stdout == "[]\n"

    @patch("phabfive.cli.diffusion._get_diffusion_app")
    def test_every_repository_left_out_by_status(self, mock_get_app):
        mock_d = MagicMock()
        mock_d.repo_list.return_value = {"repositories": [], "total": 3}
        mock_get_app.return_value = mock_d

        with patch("phabfive.cli.diffusion._get_output_format", return_value="jsonl"):
            result = runner.invoke(diffusion_app, ["repo", "list"])

        assert result.exit_code == 0
        assert result.stderr == (
            "No repositories found: the instance has 3, none of them active\n"
        )
        assert result.stdout == ""
