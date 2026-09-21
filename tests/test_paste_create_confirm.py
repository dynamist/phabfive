# -*- coding: utf-8 -*-

"""Tests for `paste create`'s confirmation flags.

`paste create` had --dry-run but no --yes or --interactive, so it was the
one command with a preview you could not act on: you either previewed and
re-ran, or created blind. Every other previewing command takes all three.
"""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from phabfive.cli.paste import paste_app

runner = CliRunner()


def _mock_paste_app(mock_get_app):
    mock_p = MagicMock()
    mock_p.create_paste_from_content.return_value = {"id": 42}
    mock_p.get_paste_url.return_value = "https://example.com/P42"
    mock_get_app.return_value = mock_p
    return mock_p


@patch("phabfive.cli.paste._get_paste_app")
def test_dry_run_still_creates_nothing(mock_get_app):
    mock_p = _mock_paste_app(mock_get_app)

    result = runner.invoke(
        paste_app, ["create", "Notes", "--content=hello", "--dry-run"]
    )

    assert result.exit_code == 0
    assert "[DRY RUN] Would create paste:" in result.output
    assert "Name: Notes" in result.output
    mock_p.create_paste_from_content.assert_not_called()


@patch("phabfive.cli.paste._get_paste_app")
def test_creating_without_flags_still_applies_directly(mock_get_app):
    mock_p = _mock_paste_app(mock_get_app)

    result = runner.invoke(paste_app, ["create", "Notes", "--content=hello"])

    assert result.exit_code == 0
    mock_p.create_paste_from_content.assert_called_once()


@patch("phabfive.cli.paste._get_paste_app")
def test_interactive_without_a_terminal_refuses(mock_get_app):
    """Same rule as every other --interactive command: no tty, no prompt."""
    mock_p = _mock_paste_app(mock_get_app)

    result = runner.invoke(paste_app, ["create", "Notes", "--content=hello", "-i"])

    assert result.exit_code == 1
    assert "--yes required for non-interactive mode" in result.output
    mock_p.create_paste_from_content.assert_not_called()


@patch("phabfive.cli.editor.confirm_apply", return_value=(False, 0))
@patch("phabfive.cli.paste._get_paste_app")
def test_interactive_declined_creates_nothing(mock_get_app, mock_confirm):
    mock_p = _mock_paste_app(mock_get_app)

    result = runner.invoke(paste_app, ["create", "Notes", "--content=hello", "-i"])

    assert "Would create paste:" in result.output
    assert "Nothing was created." in result.output
    mock_p.create_paste_from_content.assert_not_called()


@patch("phabfive.cli.editor.confirm_apply", return_value=(True, None))
@patch("phabfive.cli.paste._get_paste_app")
def test_interactive_accepted_creates(mock_get_app, mock_confirm):
    mock_p = _mock_paste_app(mock_get_app)

    result = runner.invoke(paste_app, ["create", "Notes", "--content=hello", "-i"])

    assert result.exit_code == 0
    mock_p.create_paste_from_content.assert_called_once()


@patch("phabfive.cli.paste._get_paste_app")
def test_yes_creates_without_prompting(mock_get_app):
    mock_p = _mock_paste_app(mock_get_app)

    result = runner.invoke(paste_app, ["create", "Notes", "--content=hello", "-y"])

    assert result.exit_code == 0
    mock_p.create_paste_from_content.assert_called_once()


@patch("phabfive.cli.paste._get_paste_app")
def test_yes_and_interactive_are_mutually_exclusive(mock_get_app):
    mock_p = _mock_paste_app(mock_get_app)

    result = runner.invoke(
        paste_app, ["create", "Notes", "--content=hello", "-y", "-i"]
    )

    assert result.exit_code == 1
    mock_p.create_paste_from_content.assert_not_called()


@patch("phabfive.cli.editor.confirm_apply", return_value=(False, 0))
@patch("phabfive.cli.paste._get_paste_app")
def test_the_preview_is_the_same_either_way(mock_get_app, mock_confirm):
    """--dry-run and -i show one preview, differing only in the header."""
    _mock_paste_app(mock_get_app)

    dry = runner.invoke(
        paste_app,
        ["create", "Notes", "--content=hello", "--language=python", "--dry-run"],
    )
    inter = runner.invoke(
        paste_app, ["create", "Notes", "--content=hello", "--language=python", "-i"]
    )

    body = dry.output.replace("[DRY RUN] Would create paste:", "").strip()
    assert body and body in inter.output
