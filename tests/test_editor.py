# -*- coding: utf-8 -*-
"""Tests for the confirmation helpers in phabfive.editor."""

from unittest.mock import patch

import pytest
import typer

from phabfive.editor import confirm_apply, confirm_text_change, resolve_assume_yes


class TestConfirmApply:
    """Test suite for confirm_apply."""

    def test_assume_yes_skips_the_prompt(self):
        with patch("typer.confirm") as mock_confirm:
            assert confirm_apply(True) == (True, None)
        mock_confirm.assert_not_called()

    def test_non_interactive_without_yes_fails(self, capsys):
        with patch("sys.stdin.isatty", return_value=False):
            assert confirm_apply(False) == (False, 1)

        err = capsys.readouterr().err
        assert "--yes required for non-interactive mode" in err
        # The message must name the flag the user can actually type.
        assert "--force" not in err

    def test_interactive_yes(self):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("typer.confirm", return_value=True),
        ):
            assert confirm_apply(False) == (True, None)

    def test_interactive_no(self):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("typer.confirm", return_value=False),
        ):
            assert confirm_apply(False) == (False, 0)

    def test_abort_is_not_an_error(self, capsys):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("typer.confirm", side_effect=typer.Abort()),
        ):
            assert confirm_apply(False) == (False, 0)

        assert "Cancelled" in capsys.readouterr().out

    def test_custom_prompt_is_used(self):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("typer.confirm", return_value=True) as mock_confirm,
        ):
            confirm_apply(False, prompt="Apply changes to 9 task(s)?")

        mock_confirm.assert_called_once_with("Apply changes to 9 task(s)?")


class TestConfirmTextChange:
    """Test suite for confirm_text_change."""

    def test_dry_run_skips_the_prompt_but_keeps_the_diff(self, capsys):
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("typer.confirm") as mock_confirm,
        ):
            assert confirm_text_change("old", "new", False, dry_run=True) == (
                True,
                None,
            )

        mock_confirm.assert_not_called()
        out = capsys.readouterr().out
        assert "-old" in out
        assert "+new" in out

    def test_non_interactive_without_yes_fails(self, capsys):
        with patch("sys.stdin.isatty", return_value=False):
            assert confirm_text_change("old", "new", False) == (False, 1)

        assert "--yes required" in capsys.readouterr().err

    def test_filename_appears_in_the_diff_header(self, capsys):
        with patch("sys.stdin.isatty", return_value=False):
            confirm_text_change("old", "new", True, filename="T42/title")

        out = capsys.readouterr().out
        assert "a/T42/title" in out
        assert "b/T42/title" in out


class TestResolveAssumeYes:
    """Test suite for the --force deprecation alias."""

    @pytest.mark.parametrize(
        "yes,force,expected",
        [(False, False, False), (True, False, True), (False, True, True)],
    )
    def test_either_flag_confirms(self, yes, force, expected):
        assert resolve_assume_yes(yes, force) is expected

    def test_force_warns(self, capsys):
        resolve_assume_yes(False, True)
        assert "--force is deprecated" in capsys.readouterr().err

    def test_yes_does_not_warn(self, capsys):
        resolve_assume_yes(True, False)
        assert capsys.readouterr().err == ""
