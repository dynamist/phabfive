# -*- coding: utf-8 -*-
"""Tests for space- and comma-separated ID parsing in CLI commands."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from phabfive.cli.maniphest import maniphest_app
from phabfive.cli.passphrase import passphrase_app
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


class TestManiphestShowIdParsing:
    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_comma_separated_ids(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_show.return_value = {
            "tasks": [],
            "project_names": {},
            "missing_ids": [],
        }
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["show", "T2069,T2257"])

        assert result.exit_code == 0
        assert mock_m.task_show.call_args[0][0] == [2069, 2257]

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_space_separated_ids(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_show.return_value = {
            "tasks": [],
            "project_names": {},
            "missing_ids": [],
        }
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["show", "T2069", "T2257"])

        assert result.exit_code == 0
        assert mock_m.task_show.call_args[0][0] == [2069, 2257]

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_mixed_space_and_comma(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_show.return_value = {
            "tasks": [],
            "project_names": {},
            "missing_ids": [],
        }
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["show", "T1,T2", "T3"])

        assert result.exit_code == 0
        assert mock_m.task_show.call_args[0][0] == [1, 2, 3]

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_trailing_comma_and_spaces(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_show.return_value = {
            "tasks": [],
            "project_names": {},
            "missing_ids": [],
        }
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["show", "T1, T2,"])

        assert result.exit_code == 0
        assert mock_m.task_show.call_args[0][0] == [1, 2]

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_invalid_id_in_comma_list(self, mock_get_app):
        mock_get_app.return_value = MagicMock()

        result = runner.invoke(maniphest_app, ["show", "T1,K2"])

        assert result.exit_code == 1
        assert "Invalid task ID 'K2'" in _output(result)

    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_single_id_unchanged(self, mock_get_app):
        mock_m = MagicMock()
        mock_m.task_show.return_value = {
            "tasks": [],
            "project_names": {},
            "missing_ids": [],
        }
        mock_get_app.return_value = mock_m

        result = runner.invoke(maniphest_app, ["show", "T123"])

        assert result.exit_code == 0
        assert mock_m.task_show.call_args[0][0] == [123]


class TestPasteShowIdParsing:
    @patch("phabfive.cli.paste._get_paste_app")
    def test_comma_separated_ids(self, mock_get_app):
        mock_p = MagicMock()
        mock_p.paste_show.return_value = {"pastes": [], "missing_ids": []}
        mock_get_app.return_value = mock_p

        result = runner.invoke(paste_app, ["show", "P1,P2"])

        assert result.exit_code == 0
        assert mock_p.paste_show.call_args[0][0] == [1, 2]

    @patch("phabfive.cli.paste._get_paste_app")
    def test_space_separated_ids(self, mock_get_app):
        mock_p = MagicMock()
        mock_p.paste_show.return_value = {"pastes": [], "missing_ids": []}
        mock_get_app.return_value = mock_p

        result = runner.invoke(paste_app, ["show", "P1", "P2"])

        assert result.exit_code == 0
        assert mock_p.paste_show.call_args[0][0] == [1, 2]

    @patch("phabfive.cli.paste._get_paste_app")
    def test_invalid_id_in_comma_list(self, mock_get_app):
        mock_get_app.return_value = MagicMock()

        result = runner.invoke(paste_app, ["show", "P1,X2"])

        assert result.exit_code == 1
        assert "Invalid paste ID 'X2'" in _output(result)


class TestPassphraseShowIdParsing:
    """Lock in the existing both-variant behavior of passphrase show."""

    @patch("phabfive.passphrase.display.display_passphrases")
    @patch("phabfive.cli.passphrase._get_passphrase_app")
    def test_comma_separated_ids(self, mock_get_app, mock_display):
        mock_p = MagicMock()
        mock_p.get_passphrases.return_value = [{}, {}]
        mock_get_app.return_value = mock_p

        result = runner.invoke(passphrase_app, ["show", "K1,K2"])

        assert result.exit_code == 0
        assert mock_p.get_passphrases.call_args[0][0] == ["K1", "K2"]

    @patch("phabfive.passphrase.display.display_passphrases")
    @patch("phabfive.cli.passphrase._get_passphrase_app")
    def test_space_separated_ids(self, mock_get_app, mock_display):
        mock_p = MagicMock()
        mock_p.get_passphrases.return_value = [{}, {}]
        mock_get_app.return_value = mock_p

        result = runner.invoke(passphrase_app, ["show", "K1", "K2"])

        assert result.exit_code == 0
        assert mock_p.get_passphrases.call_args[0][0] == ["K1", "K2"]


class TestManiphestEditIdParsing:
    def _invoke(self, args):
        mock_edit = MagicMock()
        mock_edit.edit_objects.return_value = 0
        with patch("phabfive.cli.maniphest._get_edit_app", return_value=mock_edit):
            result = runner.invoke(maniphest_app, ["edit", *args])
        return result, mock_edit

    def test_space_separated_ids(self):
        result, mock_edit = self._invoke(["T123", "T124", "--status=resolved"])

        assert result.exit_code == 0
        kwargs = mock_edit.edit_objects.call_args[1]
        assert kwargs["object_id"] == "T123,T124"
        assert kwargs["title"] is None

    def test_comma_separated_ids(self):
        result, mock_edit = self._invoke(["T123,T124", "--status=resolved"])

        assert result.exit_code == 0
        kwargs = mock_edit.edit_objects.call_args[1]
        assert kwargs["object_id"] == "T123,T124"
        assert kwargs["title"] is None

    def test_space_separated_ids_with_title(self):
        result, mock_edit = self._invoke(["T123", "T124", "New Title"])

        assert result.exit_code == 0
        kwargs = mock_edit.edit_objects.call_args[1]
        assert kwargs["object_id"] == "T123,T124"
        assert kwargs["title"] == "New Title"

    def test_mixed_ids_with_title(self):
        result, mock_edit = self._invoke(["T123,T124", "T125", "New Title"])

        assert result.exit_code == 0
        kwargs = mock_edit.edit_objects.call_args[1]
        assert kwargs["object_id"] == "T123,T124,T125"
        assert kwargs["title"] == "New Title"

    def test_single_id_with_title_unchanged(self):
        result, mock_edit = self._invoke(["T123", "New Title"])

        assert result.exit_code == 0
        kwargs = mock_edit.edit_objects.call_args[1]
        assert kwargs["object_id"] == "T123"
        assert kwargs["title"] == "New Title"

    def test_extra_argument_after_title_errors(self):
        result, mock_edit = self._invoke(["T123", "a title", "extra"])

        assert result.exit_code == 1
        assert "Unexpected argument 'extra'" in _output(result)
        mock_edit.edit_objects.assert_not_called()

    def test_monogram_after_title_errors(self):
        result, mock_edit = self._invoke(["T123", "a title", "T124"])

        assert result.exit_code == 1
        assert "Unexpected argument 'T124'" in _output(result)
        mock_edit.edit_objects.assert_not_called()

    def test_invalid_monogram_errors(self):
        result, mock_edit = self._invoke(["BAD", "--status=resolved"])

        assert result.exit_code == 1
        assert "Invalid task monogram 'BAD'" in _output(result)
        mock_edit.edit_objects.assert_not_called()

    def test_title_option_still_works(self):
        result, mock_edit = self._invoke(["T123", "--title", "New Title"])

        assert result.exit_code == 0
        kwargs = mock_edit.edit_objects.call_args[1]
        assert kwargs["object_id"] == "T123"
        assert kwargs["title"] == "New Title"

    def test_no_confirmation_flag(self):
        result, mock_edit = self._invoke(["T123", "--status=resolved"])

        assert result.exit_code == 0
        assert mock_edit.edit_objects.call_args[1]["force"] is False

    def test_yes_flag(self):
        result, mock_edit = self._invoke(["T123", "--status=resolved", "--yes"])

        assert result.exit_code == 0
        assert mock_edit.edit_objects.call_args[1]["force"] is True
        assert "deprecated" not in _output(result)

    def test_yes_short_flag(self):
        result, mock_edit = self._invoke(["T123", "--status=resolved", "-y"])

        assert result.exit_code == 0
        assert mock_edit.edit_objects.call_args[1]["force"] is True

    def test_force_still_works_but_warns(self):
        result, mock_edit = self._invoke(["T123", "--status=resolved", "--force"])

        assert result.exit_code == 0
        assert mock_edit.edit_objects.call_args[1]["force"] is True
        assert "--force is deprecated, use --yes instead." in _output(result)

    def test_force_is_hidden_from_help(self):
        result = runner.invoke(maniphest_app, ["edit", "--help"])

        assert result.exit_code == 0
        output = _output(result)
        assert "--yes" in output
        assert "-y" in output
        assert "--interactive" in output
        assert "-i" in output
        assert "--force" not in output

    def test_interactive_flag(self):
        result, mock_edit = self._invoke(["T123", "--status=resolved", "-i"])

        assert result.exit_code == 0
        kwargs = mock_edit.edit_objects.call_args[1]
        assert kwargs["interactive"] is True
        assert kwargs["force"] is False

    def test_no_interactive_flag(self):
        result, mock_edit = self._invoke(["T123", "--status=resolved"])

        assert result.exit_code == 0
        assert mock_edit.edit_objects.call_args[1]["interactive"] is False

    def test_yes_and_interactive_are_rejected(self):
        result, mock_edit = self._invoke(["T123", "--status=resolved", "-y", "-i"])

        assert result.exit_code == 1
        assert "mutually exclusive" in _output(result)
        mock_edit.edit_objects.assert_not_called()

    def test_force_and_interactive_are_rejected(self):
        result, mock_edit = self._invoke(["T123", "--status=resolved", "--force", "-i"])

        assert result.exit_code == 1
        assert "mutually exclusive" in _output(result)
        mock_edit.edit_objects.assert_not_called()
