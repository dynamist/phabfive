# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import click
import pytest
import typer

# phabfive imports
from phabfive.cli import app, completers
from phabfive.cli.shell_completion import escape_for_bash, install_bash_escaping


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Sprint 1", r"Sprint\ 1"),
        ("GUNNAR-Core", "GUNNAR-Core"),
        ("*", r"\*"),
        ("in:Up Next", r"in:Up\ Next"),
        ("it's $HOME", r"it\'s\ \$HOME"),
        ("a&b;(c)", r"a\&b\;\(c\)"),
    ],
)
def test_escape_for_bash(value, expected):
    assert escape_for_bash(value) == expected


def _bash_complete(monkeypatch, comp_words):
    """Run bash completion for a command line as Typer's bash script does.

    comp_words is the command line, or a list of words when a word
    contains an escaped space (bash's COMP_WORDS keeps it in one word).
    """
    words = comp_words.split(" ") if isinstance(comp_words, str) else comp_words
    monkeypatch.setenv("COMP_WORDS", "\n".join(words))
    monkeypatch.setenv("COMP_CWORD", str(len(words) - 1))

    command = typer.main.get_command(app)  # registers the completion classes
    bash = click.shell_completion.get_completion_class("bash")
    completion = bash(command, {}, "phabfive", "_PHABFIVE_COMPLETE")

    phab = MagicMock()
    phab.project.search.return_value = {
        "data": [
            {"id": 1, "phid": "PHID-PROJ-1", "fields": {"name": "Release Candidate"}},
            {"id": 2, "phid": "PHID-PROJ-2", "fields": {"name": "Sprint 1"}},
        ],
        "cursor": {"after": None},
    }
    with patch.object(
        completers,
        "_get_values_with_api_fallback",
        # --tag is the only API-backed completion these tests drive, and it no
        # longer passes [] as its default - it uses the _FETCH_FAILED sentinel
        side_effect=lambda fetch, default: fetch(phab),
    ):
        return completion.complete().split("\n")


class TestBashCompletion:
    def test_values_with_spaces_are_escaped(self, monkeypatch):
        assert _bash_complete(monkeypatch, "phabfive maniphest search --tag R") == [
            r"Release\ Candidate"
        ]

    def test_escaped_space_in_typed_text_still_matches(self, monkeypatch):
        words = ["phabfive", "maniphest", "search", "--tag", r"Release\ C"]
        result = _bash_complete(monkeypatch, words)
        assert result == [r"Release\ Candidate"]

    @pytest.mark.parametrize("quote", ['"', "'"])
    def test_quoted_word_is_not_escaped(self, monkeypatch, quote):
        result = _bash_complete(
            monkeypatch, f"phabfive maniphest search --tag {quote}Rel"
        )
        assert result == ["Release Candidate"]

    def test_commands_are_unchanged(self, monkeypatch):
        assert _bash_complete(monkeypatch, "phabfive maniphest sea") == ["search"]

    def test_install_is_idempotent(self):
        import typer._completion_classes as typer_completion

        before = typer_completion.BashComplete
        install_bash_escaping()
        assert typer_completion.BashComplete is before
        assert before.escapes_values
