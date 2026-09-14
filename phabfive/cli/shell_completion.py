# -*- coding: utf-8 -*-
"""Shell-specific adjustments to Typer's completion output."""

import os
import re

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

        base = typer_completion.BashComplete
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

    typer_completion.BashComplete = BashComplete
