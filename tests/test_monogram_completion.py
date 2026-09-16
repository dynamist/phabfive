# -*- coding: utf-8 -*-

# 3rd party imports
import pytest
import typer
from click.shell_completion import ShellComplete

# phabfive imports
from phabfive.cli import app

SUBCOMMANDS = ["edit", "passphrase", "diffusion", "paste", "user", "maniphest"]


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
        assert helps["R"] == "diffusion branch list R123"

    def test_after_global_options(self):
        assert "T" in _values(["--format", "json"], "")

    def test_typed_prefix_completes_subcommands_only(self):
        assert _values([], "p") == ["passphrase", "paste"]

    @pytest.mark.parametrize("letter", ["T", "K", "P", "R"])
    def test_bare_letter_is_not_offered(self, letter):
        """The shell would accept it with a space before the number."""
        assert _values([], letter) == []

    @pytest.mark.parametrize(
        "monogram, expansion",
        [("T123", "maniphest show T123"), ("R7", "diffusion branch list R7")],
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
