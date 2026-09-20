# -*- coding: utf-8 -*-
"""Typer-based CLI for phabfive."""

import os
import re
import sys
from importlib import resources
from importlib.metadata import version
from typing import Optional

# Disable Typer's Rich formatting for help text to remove fancy boxes
os.environ.setdefault("TYPER_USE_RICH", "0")

import typer

from phabfive import init_logging
from phabfive.cli.cache import cache_app
from phabfive.cli.diffusion import diffusion_app
from phabfive.cli.edit import edit_command
from phabfive.cli.maniphest import maniphest_app
from phabfive.cli.passphrase import passphrase_app
from phabfive.cli.paste import paste_app
from phabfive.cli.repl import repl_app
from phabfive.cli.shell_completion import MonogramGroup, install_bash_escaping
from phabfive.cli.user import user_app
from phabfive.constants import (
    AutoOption,
    COMMENTS_SUPPORTED,
    FORMAT_ALIASES,
    MONOGRAM_SHORTCUT,
    MONOGRAMS,
    OutputFormat,
)

# Build pattern dynamically from MONOGRAM_SHORTCUT keys
_MONOGRAM_PATTERN = re.compile(r"^([" + "".join(MONOGRAM_SHORTCUT.keys()) + r"])(\d+)$")

# Build set of prefix letters for apps that support comments
# e.g., ["maniphest"] -> {"T"} (extracted from MONOGRAMS["maniphest"] = "T[0-9]+")
_COMMENT_PREFIXES = {MONOGRAMS[app][0] for app in COMMENTS_SUPPORTED}

# Global flags that take no value. Monogram preprocessing otherwise assumes
# every option consumes the next argument, which would swallow the monogram
# in "phabfive -v T123" and leave it unexpanded.
_VALUELESS_GLOBAL_FLAGS = {
    "--verbose",
    "--quiet",
    "-V",
    "--version",
    "--help",
    "--install-completion",
    "--show-completion",
    "--skill",
}

# -v and -q are counted rather than valued, so -v, -vv, -q, -qq take no value
_COUNTED_SHORT_FLAG = re.compile(r"-(v+|q+)")

# The verbosity ladder -q and -v walk, quietest first
_LOG_LEVELS = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
_DEFAULT_LOG_LEVEL_INDEX = _LOG_LEVELS.index("WARNING")


def _consumes_next_arg(arg: str) -> bool:
    """Whether an option argument takes the following argv entry as its value."""
    if arg in _VALUELESS_GLOBAL_FLAGS or _COUNTED_SHORT_FLAG.fullmatch(arg):
        return False
    if arg.startswith("--"):
        return "=" not in arg
    # Short option like -f value
    return len(arg) == 2


# Insert completion values with spaces (e.g. project names) as one bash word
install_bash_escaping()

# Main app
app = typer.Typer(
    cls=MonogramGroup,
    name="phabfive",
    help="CLI for Phabricator and Phorge - built for humans and AI agents.",
    no_args_is_help=True,
    # "phabfive -v" has a non-empty argv, so no_args_is_help never fires and
    # click would answer with a bare "Missing command.". Let the callback run
    # so it can print the command list instead.
    invoke_without_command=True,
    add_completion=True,
)


def preprocess_format_alias(argv: list[str]) -> list[str]:
    """Rewrite accepted --format spellings to the OutputFormat they mean.

    --format=strict is the old name for yaml, --format=ndjson the other
    common name for jsonl. Rewriting here, before Typer parses, keeps both
    out of the enum and out of every branch that tests the format.
    """
    result = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        alias = None
        if arg.startswith("--format="):
            alias = FORMAT_ALIASES.get(arg.removeprefix("--format="))
        if alias is not None:
            result.append(f"--format={alias}")
        elif arg == "--format" and i + 1 < len(argv) and argv[i + 1] in FORMAT_ALIASES:
            result.append("--format")
            result.append(FORMAT_ALIASES[argv[i + 1]])
            i += 1  # Skip the next arg since we handled it
        else:
            result.append(arg)
        i += 1
    return result


def preprocess_monograms(argv: list[str]) -> list[str]:
    """Expand monogram shortcuts in argv before Typer parsing.

    Examples:
        T123 → maniphest show T123
        --format=yaml T123 → --format=yaml maniphest show T123
        T123 'comment' → maniphest comment T123 'comment'
        edit T123 → maniphest edit T123
        K123 → passphrase show K123
        P123 → paste show P123
        R123 → diffusion repo show R123
    """
    if len(argv) < 2:
        return argv

    # Find first positional argument (skip options and their values)
    def find_first_positional():
        skip_next = False
        for i, arg in enumerate(argv[1:], start=1):
            if skip_next:
                skip_next = False
                continue
            if arg.startswith("-"):
                skip_next = _consumes_next_arg(arg)
                continue
            return i
        return None

    # Handle "edit <monogram>" pattern: edit T123 → maniphest edit T123
    first_pos_idx = find_first_positional()
    if first_pos_idx and argv[first_pos_idx] == "edit":
        next_idx = first_pos_idx + 1
        if next_idx < len(argv):
            match = _MONOGRAM_PATTERN.match(argv[next_idx])
            if match:
                prefix = match.group(1)
                app_name = MONOGRAM_SHORTCUT[prefix][0]  # e.g., "maniphest"
                # edit T123 --opts → maniphest edit T123 --opts
                before = argv[:first_pos_idx]
                after = argv[next_idx:]  # includes T123 and rest
                return before + [app_name, "edit"] + after

    # Find the first non-option argument that matches a monogram
    monogram_idx = None
    non_option_args = []  # Track non-option args before monogram
    skip_next = False
    for i, arg in enumerate(argv[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            skip_next = _consumes_next_arg(arg)
            continue
        match = _MONOGRAM_PATTERN.match(arg)
        if match:
            monogram_idx = i
            break
        non_option_args.append(arg)

    if monogram_idx is None:
        return argv

    monogram = argv[monogram_idx]
    match = _MONOGRAM_PATTERN.match(monogram)
    prefix = match.group(1)
    expansion = MONOGRAM_SHORTCUT[prefix]

    # Only expand if monogram is the first positional argument
    # If there are other args before it, the monogram is being used as an argument
    # to another command (e.g., "maniphest parents T123")
    if non_option_args:
        return argv

    # Split argv into: before monogram, monogram, after monogram
    before = argv[:monogram_idx]
    after = argv[monogram_idx + 1 :]

    # Handle comment shortcut: T123 'text' → maniphest comment T123 'text'
    # But not when the next arg is also a monogram (T123 T456 → show both)
    if (
        prefix in _COMMENT_PREFIXES
        and after
        and not after[0].startswith("-")
        and not _MONOGRAM_PATTERN.match(after[0])
    ):
        app_name = expansion[0]  # e.g., 'maniphest'
        return before + [app_name, "comment", monogram] + after

    return before + expansion + [monogram] + after


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        typer.echo(version("phabfive"))
        raise typer.Exit()


def skill_callback(value: bool) -> None:
    """Print the agent skill file and exit.

    Printed verbatim, trailing newline and all, so the output can be written
    straight to a SKILL.md an agent loads.
    """
    if value:
        skill = resources.files("phabfive").joinpath("SKILL.md")
        typer.echo(skill.read_text(encoding="utf-8"), nl=False)
        raise typer.Exit()


def resolve_log_level(verbose: int, quiet: int) -> str:
    """Resolve repeated -v and -q into one log level.

    The two count against each other along one ladder, so -v and -q cancel
    out and neither can be pushed past its end.
    """
    step = verbose - quiet
    index = _DEFAULT_LOG_LEVEL_INDEX + step

    # Saturate rather than wrap, so -vvv stays at DEBUG and -qqq at CRITICAL
    index = max(0, min(index, len(_LOG_LEVELS) - 1))

    return _LOG_LEVELS[index]


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    verbose: int = typer.Option(
        0,
        "-v",
        "--verbose",
        count=True,
        help="Increase verbosity: -v for INFO, -vv for DEBUG.",
    ),
    quiet: int = typer.Option(
        0,
        "-q",
        "--quiet",
        count=True,
        help="Decrease verbosity: -q for ERROR, -qq for CRITICAL.",
    ),
    output_format: Optional[OutputFormat] = typer.Option(
        None,
        "--format",
        help="Output format. Auto-detects based on TTY.",
    ),
    ascii_when: AutoOption = typer.Option(
        AutoOption.auto,
        "--ascii",
        help="Use ASCII instead of Unicode.",
    ),
    hyperlink_when: AutoOption = typer.Option(
        AutoOption.auto,
        "--hyperlink",
        help="Enable terminal hyperlinks.",
    ),
    version: bool = typer.Option(
        False,
        "-V",
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Display the version number and exit",
    ),
    skill: bool = typer.Option(
        False,
        "--skill",
        callback=skill_callback,
        is_eager=True,
        help="Print the agent skill file and exit",
    ),
) -> None:
    """CLI for Phabricator and Phorge - built for humans and AI agents."""
    # Configure logging before anything can log
    effective_log_level = resolve_log_level(verbose, quiet)
    init_logging(effective_log_level)

    # Store global options in context for subcommands to access
    ctx.ensure_object(dict)
    # Store as string values for downstream compatibility
    ctx.obj["log_level"] = effective_log_level
    ctx.obj["format"] = output_format.value if output_format else None
    ctx.obj["ascii"] = ascii_when.value
    ctx.obj["hyperlink"] = hyperlink_when.value

    # Global options but no command: show what the commands are
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help(), err=True)
        raise typer.Exit(2)


app.add_typer(cache_app, name="cache")
app.add_typer(passphrase_app, name="passphrase")
app.add_typer(diffusion_app, name="diffusion")
app.command(name="edit")(edit_command)
app.add_typer(paste_app, name="paste")
app.add_typer(user_app, name="user")
app.add_typer(maniphest_app, name="maniphest")
# Show repl command only if ptpython is installed (phabfive[repl])
try:
    import ptpython  # noqa: F401

    _repl_hidden = False
except ImportError:
    _repl_hidden = True
app.add_typer(repl_app, name="repl", hidden=_repl_hidden)


def cli_entrypoint() -> None:
    """Main entry point for the phabfive CLI (Typer version)."""
    import os

    # Verify working directory exists
    try:
        os.getcwd()
    except FileNotFoundError:
        typer.echo(
            "Error: Current working directory does not exist.",
            err=True,
        )
        raise typer.Exit(1)

    # Preprocess argv before Typer sees the args
    sys.argv = preprocess_format_alias(sys.argv)
    sys.argv = preprocess_monograms(sys.argv)

    try:
        app()
    except KeyboardInterrupt:
        raise typer.Exit(130)
