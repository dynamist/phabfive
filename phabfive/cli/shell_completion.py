# -*- coding: utf-8 -*-
"""Shell-specific adjustments to Typer's completion output."""

import os
import re
from typing import Any

from click.shell_completion import CompletionItem

from phabfive.cli.agents import AgentFooterGroup
from phabfive.constants import MONOGRAM_SHORTCUT

# A monogram shortcut such as T123, K5 or R12
_MONOGRAM = re.compile(r"^[" + "".join(MONOGRAM_SHORTCUT) + r"]\d+$")

# Characters that bash would otherwise treat as word separators or syntax
_BASH_SPECIAL_CHARS = re.compile(r"([\s\\'\"`$&;|()<>*?\[\]{}!#~])")


def escape_for_bash(value: str) -> str:
    """Backslash-escape a completion value so bash inserts it as one word.

    For example "Kanban Board" becomes "Kanban\\ Board".
    """
    return _BASH_SPECIAL_CHARS.sub(r"\\\1", value)


def install_bash_escaping() -> None:
    """Make bash completion insert values with spaces as a single argument.

    Typer's bash completion prints values as-is, so bash inserts
    "Kanban Board" as two words. Values are left unescaped when the word
    being completed starts with a quote, since bash keeps the quote.

    Typer registers its completion classes
    each time the app runs, looking up BashComplete in its own module, so
    the subclass is installed there. This relies on Typer's private
    _completion_classes module; if that changes, completion keeps working
    without the escaping.
    """
    try:
        import typer._completion_classes as typer_completion

        # Any, because a class held in a variable cannot be a base class to
        # mypy, and this one is private to Typer anyway
        base: Any = typer_completion.BashComplete
    except (ImportError, AttributeError):
        return

    if getattr(base, "escapes_values", False):
        return

    class BashComplete(base):
        escapes_values = True
        word_is_quoted = False

        def get_completion_args(self):
            # The completion script joins COMP_WORDS with newlines; the parsed
            # args have their quotes removed, so check the raw word
            words = os.environ.get("COMP_WORDS", "").split("\n")
            cword = int(os.environ.get("COMP_CWORD", 0))
            raw_word = words[cword] if cword < len(words) else ""
            self.word_is_quoted = raw_word[:1] in ("'", '"')
            return super().get_completion_args()

        def format_completion(self, item) -> str:
            if self.word_is_quoted:
                return item.value
            return escape_for_bash(item.value)

    typer_completion.BashComplete = BashComplete  # type: ignore[misc]


def _monogram_help(monogram: str) -> str:
    """Describe what a monogram shortcut expands to, e.g. "maniphest show T123"."""
    expansion = " ".join(MONOGRAM_SHORTCUT[monogram[0]])
    return f"{expansion} {monogram}"


class MonogramGroup(AgentFooterGroup):
    """Root command group that also completes monogram shortcuts.

    phabfive T123 expands to "maniphest show T123" (see preprocess_monograms),
    but shell completion only knew the subcommands. With nothing typed yet,
    the monogram letters are offered alongside the subcommands, described
    with an example in zsh and fish. A bare letter is not offered once typed,
    since the shell would accept it with a trailing space before the number.
    A complete monogram such as T123 is accepted as typed.
    """

    def resolve_command(self, ctx, args):
        """Resolve a leading monogram to the command it stands for.

        Completion resolves the words the shell passes, while monograms are
        expanded in cli_entrypoint from sys.argv, so without this
        "phabfive T123 --<TAB>" offered the global options instead of the
        options of "maniphest show".
        """
        if args and _MONOGRAM.match(args[0]):
            # Imported here because phabfive.cli imports this module
            from phabfive.cli import preprocess_monograms

            args = preprocess_monograms(["phabfive", *args])[1:]

        return super().resolve_command(ctx, args)

    def shell_complete(self, ctx, incomplete):
        if _MONOGRAM.match(incomplete):
            return [CompletionItem(incomplete, help=_monogram_help(incomplete))]

        completions = super().shell_complete(ctx, incomplete)
        if not incomplete:
            completions.extend(
                CompletionItem(letter, help=_monogram_help(f"{letter}123"))
                for letter in MONOGRAM_SHORTCUT
            )
        return completions
