# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest

# phabfive imports
from phabfive.cli import completers
from phabfive.cli.completers import (
    DEFAULT_PRIORITY_VALUES,
    DEFAULT_STATUS_VALUES,
    PATTERN_PREFIXES,
    complete_column,
    complete_column_change,
    complete_column_filter,
    complete_priority,
    complete_priority_change,
    complete_priority_filter,
    complete_status,
    complete_status_filter,
)


@pytest.fixture(autouse=True)
def no_api():
    """Use the default values instead of calling the API."""
    with patch.object(
        completers,
        "_get_values_with_api_fallback",
        side_effect=lambda fetch, default: default,
    ):
        yield


@pytest.fixture
def board_columns():
    """Fake the board lookup and record which board was asked for."""
    with patch.object(
        completers,
        "_get_board_columns",
        side_effect=lambda tag: ["Backlog", "Up Next", "Done"],
    ) as mock:
        yield mock


def _ctx(**params):
    ctx = MagicMock()
    ctx.params = params
    return ctx


def _has_pattern_syntax(completions):
    return any(":" in c or c in ("*", "raised", "lowered") for c in completions)


class TestPriorityCompletion:
    def test_create_offers_only_priority_names(self):
        assert complete_priority("") == DEFAULT_PRIORITY_VALUES

    def test_create_matches_prefix(self):
        assert complete_priority("h") == ["high"]

    def test_edit_adds_raise_lower(self):
        result = complete_priority_change("")
        assert result == DEFAULT_PRIORITY_VALUES + ["raise", "lower"]
        assert not _has_pattern_syntax(result)

    def test_edit_matches_prefix(self):
        assert complete_priority_change("l") == ["low", "lower"]

    def test_search_offers_patterns_and_keywords(self):
        result = complete_priority_filter("")
        assert "high" in result
        assert "in:high" in result
        assert "not:in:high" in result
        assert "raised" in result and "lowered" in result

    def test_search_completes_after_prefix(self):
        assert complete_priority_filter("from:h") == ["from:high"]

    def test_search_keyword_prefix(self):
        assert complete_priority_filter("r") == ["raised"]


class TestStatusCompletion:
    def test_create_and_edit_offer_only_status_names(self):
        result = complete_status("")
        assert result == DEFAULT_STATUS_VALUES
        assert not _has_pattern_syntax(result)

    def test_search_offers_patterns_and_keywords(self):
        result = complete_status_filter("")
        assert "open" in result
        assert "in:open" in result
        assert "raised" in result and "lowered" in result

    def test_search_completes_after_prefix(self):
        assert complete_status_filter("in:") == [
            f"in:{s}" for s in DEFAULT_STATUS_VALUES
        ]


class TestColumnCompletion:
    def test_create_offers_board_columns_only(self, board_columns):
        result = complete_column(_ctx(tag=["Sprint"]), [], "")
        assert result == ["Backlog", "Up Next", "Done"]

    def test_create_uses_first_repeated_tag_as_board(self, board_columns):
        """--tag is repeatable on create, so ctx.params holds a tuple."""
        complete_column(_ctx(tag=("Sprint", "Backend")), [], "")
        board_columns.assert_called_once_with("Sprint")

    def test_create_uses_first_comma_separated_tag_as_board(self, board_columns):
        """--tag=Board,Other names two tags, the first of them the board."""
        complete_column(_ctx(tag=["Board,Other"]), [], "")
        board_columns.assert_called_once_with("Board")

    def test_create_without_tag_offers_nothing(self, board_columns):
        assert complete_column(_ctx(tag=None), [], "") == []
        assert complete_column(_ctx(tag=()), [], "") == []
        board_columns.assert_not_called()

    def test_edit_adds_forward_backward(self, board_columns):
        result = complete_column_change(_ctx(tag="Sprint"), [], "")
        assert result == ["forward", "backward", "Backlog", "Up Next", "Done"]

    def test_edit_matches_case_insensitively(self, board_columns):
        assert complete_column_change(_ctx(tag="Sprint"), [], "up") == ["Up Next"]

    def test_search_offers_patterns_wildcard_and_columns(self, board_columns):
        result = complete_column_filter(_ctx(tag="Sprint"), [], "")
        assert result[:2] == ["forward", "backward"]
        for prefix in PATTERN_PREFIXES:
            assert prefix in result
        assert "*" in result
        assert result[-3:] == ["Backlog", "Up Next", "Done"]

    def test_search_without_tag_skips_columns(self, board_columns):
        result = complete_column_filter(_ctx(tag=None), [], "")
        assert "*" in result
        board_columns.assert_not_called()


class TestSpecFileCompletion:
    """`-f`/`--spec` completes a path, filtered to the formats that load.

    `--with`, the deprecated spelling of the same files, never had a
    completer: it was the one file-taking option in the tree with none, and
    TAB on it offered every file in the directory, a .png as readily as a
    spec. `-f` is what replaces it, so `-f` is what gets the behaviour
    (#491).

    The shells are driven one at a time rather than trusted to share a code
    path, because they do not: each of Typer's completion classes parses its
    own environment and formats its own output, and phabfive replaces bash's
    formatter to escape spaces. A completer that works in-process can still
    be unusable in one shell.
    """

    def _command(self, name):
        from typer.main import get_command

        from phabfive.cli import app

        return get_command(app).commands[name]

    def _option(self, command_name, spelling):
        command = self._command(command_name)
        [option] = [one for one in command.params if spelling in one.opts]
        return option

    @pytest.mark.parametrize("command_name", ["apply", "search"])
    @pytest.mark.parametrize("spelling", ["-f", "--spec"])
    def test_the_option_carries_the_completer(self, command_name, spelling):
        """`shell_complete is not None` would be vacuously true.

        It is a bound method on every click Parameter, so an option with no
        completer at all satisfies that assertion too. A custom completer
        lands in `_custom_shell_complete`.
        """
        option = self._option(command_name, spelling)

        # Typer wraps the callable in a compatibility shim, so what is
        # asserted is that a custom completer is wired at all; that it is
        # *this* one is asserted below by driving it and getting spec files
        # back.
        assert option._custom_shell_complete is not None

    @pytest.mark.parametrize("command_name", ["apply", "search"])
    def test_tab_on_the_option_offers_spec_files_only(
        self, command_name, tmp_path, monkeypatch
    ):
        import click

        (tmp_path / "sprint-tasks.yaml").touch()
        (tmp_path / "notes.md").touch()
        monkeypatch.chdir(tmp_path)

        option = self._option(command_name, "-f")
        context = click.Context(click.Command(command_name))

        offered = option.shell_complete(context, "s")

        assert [one.value for one in offered] == ["sprint-tasks.yaml"]

    def test_a_directory_keeps_completion_walking(self, tmp_path, monkeypatch):
        import os

        import click

        (tmp_path / "specs").mkdir()
        (tmp_path / "specs" / "sprint.yaml").touch()
        monkeypatch.chdir(tmp_path)

        option = self._option("apply", "-f")
        context = click.Context(click.Command("apply"))

        assert [one.value for one in option.shell_complete(context, "sp")] == [
            f"specs{os.sep}"
        ]
        assert [
            one.value for one in option.shell_complete(context, f"specs{os.sep}")
        ] == [f"specs{os.sep}sprint.yaml"]

    def test_a_leading_tilde_is_expanded_and_kept(self, tmp_path, monkeypatch):
        """`-f ~/spec<TAB>` is a path the shell has not expanded yet.

        bash hands the word over with the tilde still on it, so a completer
        that stats it verbatim finds nothing. It is expanded to read the
        directory and written back unexpanded, because the shell expands it
        again when the command runs.
        """
        home = tmp_path / "home"
        (home / "specs").mkdir(parents=True)
        (home / "specs" / "sprint.yaml").touch()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))

        assert completers.complete_spec_file("~/specs/") == ["~/specs/sprint.yaml"]


class TestSpecFileCompletionInEveryShell:
    """The same TAB, through each shell's own class, end to end.

    `complete()` is the whole path a shell runs: read the environment the
    completion script exports, resolve the command, run the completer, format
    every item the way that shell parses. Nothing here is mocked but the
    directory the shell is standing in.
    """

    def _complete(self, shell, monkeypatch, line, tmp_path):
        import click
        import typer

        from phabfive.cli import app

        words = line.split(" ")

        if shell == "bash":
            # bash exports the words and the index of the one being typed
            monkeypatch.setenv("COMP_WORDS", "\n".join(words))
            monkeypatch.setenv("COMP_CWORD", str(len(words) - 1))
        else:
            # Typer's own zsh and fish scripts export the command line
            # instead, and split the last word off it unless it ends in a
            # space. This is what those two shells actually run, which is why
            # it is written out rather than shared with bash.
            monkeypatch.setenv("_TYPER_COMPLETE_ARGS", line)

        if shell == "fish":
            # Fish asks twice: once whether there is anything to offer, then
            # for the values. "get-args" is the second call, and the only one
            # that returns text rather than an exit status.
            monkeypatch.setenv("_TYPER_COMPLETE_FISH_ACTION", "get-args")

        monkeypatch.chdir(tmp_path)

        command = typer.main.get_command(app)  # registers the classes
        completion_class = click.shell_completion.get_completion_class(shell)
        completion = completion_class(command, {}, "phabfive", "_PHABFIVE_COMPLETE")

        return completion.complete()

    @pytest.fixture
    def corpus(self, tmp_path):
        (tmp_path / "sprint-tasks.yaml").touch()
        (tmp_path / "audit.toml").touch()
        (tmp_path / "notes.md").touch()
        return tmp_path

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    @pytest.mark.parametrize("command_name", ["apply", "search"])
    def test_the_spec_files_are_offered(self, shell, command_name, corpus, monkeypatch):
        output = self._complete(
            shell, monkeypatch, f"phabfive {command_name} -f ", corpus
        )

        assert "sprint-tasks.yaml" in output
        assert "audit.toml" in output
        assert "notes.md" not in output

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_a_typed_prefix_narrows_the_offer(self, shell, corpus, monkeypatch):
        output = self._complete(shell, monkeypatch, "phabfive apply -f spr", corpus)

        assert "sprint-tasks.yaml" in output
        assert "audit.toml" not in output

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_the_long_spelling_completes_too(self, shell, corpus, monkeypatch):
        output = self._complete(shell, monkeypatch, "phabfive search --spec ", corpus)

        assert "sprint-tasks.yaml" in output

    def test_bash_escapes_a_path_with_a_space(self, tmp_path, monkeypatch):
        """bash would otherwise insert `my specs/` as two words."""
        (tmp_path / "my specs").mkdir()

        output = self._complete("bash", monkeypatch, "phabfive apply -f my", tmp_path)

        assert output.splitlines() == [r"my\ specs/"]
