# -*- coding: utf-8 -*-

"""What a search that found nothing prints on stdout (#522).

`json` and `yaml` are one document holding a list, so nothing found is an
empty list: zero bytes is not a JSON document, and it is also what a command
that failed prints, so a program could not tell the two apart. `jsonl` is
zero records, which is zero lines, and the terminal formats print nothing
because the command says "No ... found" on stderr.
"""

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app
from phabfive.core import Phabfive
from phabfive.display import display_empty, display_tasks

runner = CliRunner()

EMPTY = {"json": "[]\n", "yaml": "[]\n", "jsonl": "", "rich": "", "table": ""}


@pytest.fixture
def restore_output_format():
    """`--format` sets a class-level default; put it back after the test."""
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


@pytest.mark.parametrize("output_format, expected", EMPTY.items())
def test_display_empty(output_format, expected, capsys):
    display_empty(output_format)

    assert capsys.readouterr().out == expected


class TestDisplayTasks:
    def test_no_tasks_is_an_empty_list(self, capsys):
        display_tasks({"tasks": []}, "json", MagicMock())

        assert capsys.readouterr().out == "[]\n"

    def test_a_failed_search_prints_nothing(self, capsys):
        """None is a failure, and `[]` for it would look like an answer."""
        display_tasks(None, "json", MagicMock())

        assert capsys.readouterr().out == ""


def _maniphest():
    maniphest = MagicMock()
    maniphest.task_search.return_value = {"tasks": []}
    return patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest)


def _paste():
    paste = MagicMock()
    paste.paste_search.return_value = {"pastes": []}
    return patch("phabfive.cli.paste._get_paste_app", return_value=paste)


def _passphrase():
    passphrase = MagicMock()
    passphrase.search_passphrases.return_value = []
    return patch("phabfive.cli.passphrase._get_passphrase_app", return_value=passphrase)


SEARCHES = {
    "maniphest": (["maniphest", "search", "--assigned=@me"], _maniphest),
    "paste": (["paste", "search", "nope"], _paste),
    "passphrase": (["passphrase", "search", "nope"], _passphrase),
}


@pytest.mark.parametrize("output_format", ["json", "yaml", "jsonl"])
@pytest.mark.parametrize("command", SEARCHES)
def test_an_empty_search(command, output_format, restore_output_format):
    argv, patched = SEARCHES[command]

    with patched():
        result = runner.invoke(app, [f"--format={output_format}", *argv])

    assert result.exit_code == 0
    assert result.stdout == EMPTY[output_format]
