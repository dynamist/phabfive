# -*- coding: utf-8 -*-
"""Tests for the confirmation helpers in phabfive.editor."""

import io
from unittest.mock import patch

import pytest
import typer

from phabfive.editor import (
    confirm_apply,
    confirm_text_change,
    open_tty,
    prompt_each,
    render_changes,
    resolve_assume_yes,
)


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

    @pytest.mark.parametrize("yes,force", [(True, False), (False, True)])
    def test_yes_and_interactive_are_mutually_exclusive(self, yes, force):
        with pytest.raises(ValueError, match="mutually exclusive"):
            resolve_assume_yes(yes, force, interactive=True)

    def test_interactive_alone_is_fine(self):
        assert resolve_assume_yes(False, False, interactive=True) is False


class FakeTty:
    """A terminal whose reads and writes do not share a position."""

    def __init__(self, answers=""):
        self._reader = io.StringIO(answers)
        self.written = io.StringIO()

    def write(self, text):
        self.written.write(text)

    def flush(self):
        pass

    def readline(self):
        return self._reader.readline()


class TestRenderChanges:
    """Test suite for render_changes."""

    def test_structured_fields_render_as_old_to_new(self, capsys):
        render_changes(
            "T42",
            [
                {"field": "Status", "old": "Open", "new": "Resolved"},
                {"field": "Column", "old": "Backlog", "new": "Done"},
            ],
        )

        out = capsys.readouterr().out
        assert "T42:" in out
        assert "Status: Open \u2192 Resolved" in out
        assert "Column: Backlog \u2192 Done" in out

    def test_title_renders_as_a_diff(self, capsys):
        render_changes("T42", [{"field": "Title", "old": "before", "new": "after"}])

        out = capsys.readouterr().out
        assert "a/title" in out
        assert "-before" in out
        assert "+after" in out

    def test_none_old_means_added(self, capsys):
        render_changes("T42", [{"field": "Comment", "old": None, "new": "hello"}])

        assert "Comment: hello" in capsys.readouterr().out

    def test_header_overrides_the_monogram_line(self, capsys):
        render_changes(
            "T42",
            [{"field": "Status", "old": "Open", "new": "Resolved"}],
            header="[DRY RUN] Would apply to T42:",
        )

        out = capsys.readouterr().out
        assert "[DRY RUN] Would apply to T42:" in out
        assert not out.startswith("T42:")


class TestPromptEach:
    """Test suite for the per-task review prompt."""

    @pytest.mark.parametrize(
        "answer,expected", [("y", "y"), ("n", "n"), ("a", "a"), ("q", "q")]
    )
    def test_each_key(self, answer, expected):
        assert prompt_each("T1", FakeTty(f"{answer}\n")) == expected

    def test_case_and_whitespace_are_forgiven(self):
        assert prompt_each("T1", FakeTty("  Y  \n")) == "y"

    def test_only_the_first_character_counts(self):
        assert prompt_each("T1", FakeTty("yes\n")) == "y"

    def test_unrecognised_input_reprompts_with_the_legend(self):
        tty = FakeTty("zz\ny\n")

        assert prompt_each("T1", tty) == "y"

        written = tty.written.getvalue()
        assert "y - apply this change" in written
        assert "q - quit, applying nothing further" in written
        assert written.count("Apply T1?") == 2

    def test_question_mark_shows_the_legend(self):
        tty = FakeTty("?\nn\n")

        assert prompt_each("T1", tty) == "n"
        assert "a - apply this and all remaining" in tty.written.getvalue()

    def test_bare_enter_reprompts(self):
        tty = FakeTty("\n\ny\n")

        assert prompt_each("T1", tty) == "y"
        assert tty.written.getvalue().count("Apply T1?") == 3

    def test_eof_quits_rather_than_assuming_consent(self):
        assert prompt_each("T1", FakeTty("")) == "q"

    def test_prompt_names_the_object(self):
        tty = FakeTty("y\n")
        prompt_each("T99", tty)
        assert "Apply T99? [y,n,a,q,?]" in tty.written.getvalue()


class TestOpenTty:
    """Test suite for open_tty."""

    def test_returns_none_without_a_controlling_terminal(self):
        with patch("builtins.open", side_effect=OSError("no tty")):
            assert open_tty() is None

    def test_returns_the_handle_when_there_is_one(self):
        handle = object()
        with patch("builtins.open", return_value=handle) as mock_open:
            assert open_tty() is handle
        mock_open.assert_called_once_with("/dev/tty", "r+")
