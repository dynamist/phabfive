# -*- coding: utf-8 -*-

# 3rd party imports
import pytest
import typer
from click.shell_completion import ShellComplete

# phabfive imports
from phabfive.cli import app

SUBCOMMANDS = [
    "edit",
    "cache",
    "passphrase",
    "diffusion",
    "paste",
    "project",
    "user",
    "maniphest",
    "spec",
]


def _complete(args, incomplete):
    """Return (value, help) for what shell completion offers."""
    command = typer.main.get_command(app)
    completion = ShellComplete(command, {}, "phabfive", "_PHABFIVE_COMPLETE")
    return [
        (item.value, item.help) for item in completion.get_completions(args, incomplete)
    ]


def _values(args, incomplete):
    return [value for value, _ in _complete(args, incomplete)]


class TestRootCompletion:
    def test_offers_subcommands_and_monogram_letters(self):
        assert _values([], "") == SUBCOMMANDS + ["T", "K", "P", "R"]

    def test_letters_describe_the_shortcut(self):
        helps = dict(_complete([], ""))
        assert helps["T"] == "maniphest show T123"
        assert helps["K"] == "passphrase show K123"
        assert helps["P"] == "paste show P123"
        assert helps["R"] == "diffusion repo show R123"

    def test_after_global_options(self):
        assert "T" in _values(["--format", "json"], "")

    def test_typed_prefix_completes_subcommands_only(self):
        assert _values([], "p") == ["passphrase", "paste", "project"]

    @pytest.mark.parametrize("letter", ["T", "K", "P", "R"])
    def test_bare_letter_is_not_offered(self, letter):
        """The shell would accept it with a space before the number."""
        assert _values([], letter) == []

    @pytest.mark.parametrize(
        "monogram, expansion",
        [("T123", "maniphest show T123"), ("R7", "diffusion repo show R7")],
    )
    def test_complete_monogram_is_accepted(self, monogram, expansion):
        assert _complete([], monogram) == [(monogram, expansion)]

    @pytest.mark.parametrize("text", ["X123", "T12a", "t123"])
    def test_other_text_is_not_a_monogram(self, text):
        assert _values([], text) == []

    def test_options_still_complete(self):
        assert "--format" in _values([], "--f")

    def test_subcommand_completion_unchanged(self):
        assert _values(["maniphest"], "s") == ["show", "search", "subtasks"]


class TestCompletionAfterMonogram:
    """A leading monogram resolves to the command it stands for."""

    @pytest.mark.parametrize(
        "monogram_args, expanded_args",
        [
            (["T123"], ["maniphest", "show", "T123"]),
            (["K1"], ["passphrase", "show", "K1"]),
            (["P1"], ["paste", "show", "P1"]),
            (["R1"], ["diffusion", "repo", "show", "R1"]),
            # T123 "text" is the comment shortcut
            (["T123", "hello"], ["maniphest", "comment", "T123", "hello"]),
            # Global options may come before the monogram
            (
                ["--format", "json", "T123"],
                ["--format", "json", "maniphest", "show", "T123"],
            ),
        ],
    )
    def test_offers_the_same_as_the_expanded_command(
        self, monogram_args, expanded_args
    ):
        expected = _values(expanded_args, "--")
        assert expected  # the expanded command has options to offer
        assert _values(monogram_args, "--") == expected

    def test_show_options_are_offered(self):
        assert _values(["T123"], "--show") == [
            "--show-history",
            "--show-metadata",
            "--show-comments",
            "--show-policy",
        ]

    def test_monogram_as_a_command_argument_is_untouched(self):
        """Only a leading monogram expands, not one passed to a command."""
        assert _values(["maniphest", "parents", "T123"], "--") == _values(
            ["maniphest", "parents"], "--"
        )

    def test_edit_shortcut_still_resolves(self):
        assert _values(["edit", "T123"], "--") == _values(
            ["maniphest", "edit", "T123"], "--"
        )
