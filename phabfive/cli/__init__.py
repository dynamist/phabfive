# -*- coding: utf-8 -*-
"""Typer-based CLI for phabfive."""

import os
import re
import sys
from importlib.metadata import version
from typing import Optional

# Disable Typer's Rich formatting for help text to remove fancy boxes
os.environ.setdefault("TYPER_USE_RICH", "0")

import typer

from phabfive.cli.diffusion import diffusion_app
from phabfive.cli.maniphest import maniphest_app
from phabfive.cli.passphrase import passphrase_app
from phabfive.cli.paste import paste_app
from phabfive.cli.repl import repl_app
from phabfive.cli.user import user_app
from phabfive.constants import (
    AutoOption,
    COMMENTS_SUPPORTED,
    LogLevel,
    MONOGRAM_SHORTCUT,
    MONOGRAMS,
    OutputFormat,
)

# Build pattern dynamically from MONOGRAM_SHORTCUT keys
_MONOGRAM_PATTERN = re.compile(r"^([" + "".join(MONOGRAM_SHORTCUT.keys()) + r"])(\d+)$")

# Build set of prefix letters for apps that support comments
# e.g., ["maniphest"] -> {"T"} (extracted from MONOGRAMS["maniphest"] = "T[0-9]+")
_COMMENT_PREFIXES = {MONOGRAMS[app][0] for app in COMMENTS_SUPPORTED}

# Main app
app = typer.Typer(
    name="phabfive",
    help="CLI for Phabricator and Phorge - built for humans and AI agents.",
    no_args_is_help=True,
    add_completion=True,
)


def preprocess_format_alias(argv: list[str]) -> list[str]:
    """Replace --format=strict with --format=yaml for backwards compatibility."""
    result = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--format=strict":
            result.append("--format=yaml")
        elif arg == "--format" and i + 1 < len(argv) and argv[i + 1] == "strict":
            result.append("--format")
            result.append("yaml")
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
        K123 → passphrase show K123
        P123 → paste show P123
        R123 → diffusion branch list R123
    """
    if len(argv) < 2:
        return argv

    # Find the first non-option argument that matches a monogram
    monogram_idx = None
    non_option_args = []  # Track non-option args before monogram
    skip_next = False
    for i, arg in enumerate(argv[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            # Handle --option value (skip next arg if not --option=value)
            if arg.startswith("--") and "=" not in arg:
                skip_next = True
            elif arg.startswith("-") and len(arg) == 2:
                # Short option like -f value
                skip_next = True
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

    # Check if already expanded (non-option args before monogram match expansion)
    if non_option_args == expansion:
        return argv

    # Split argv into: before monogram, monogram, after monogram
    before = argv[:monogram_idx]
    after = argv[monogram_idx + 1 :]

    # Handle comment shortcut: T123 'text' → maniphest comment T123 'text'
    if prefix in _COMMENT_PREFIXES and after and not after[0].startswith("-"):
        app_name = expansion[0]  # e.g., 'maniphest'
        return before + [app_name, "comment", monogram] + after

    return before + expansion + [monogram] + after


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        typer.echo(version("phabfive"))
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    log_level: LogLevel = typer.Option(
        LogLevel.INFO,
        "--log-level",
        help="Set log level.",
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
) -> None:
    """CLI for Phabricator and Phorge - built for humans and AI agents."""
    # Store global options in context for subcommands to access
    ctx.ensure_object(dict)
    # Store as string values for downstream compatibility
    ctx.obj["log_level"] = log_level.value
    ctx.obj["format"] = output_format.value if output_format else None
    ctx.obj["ascii"] = ascii_when.value
    ctx.obj["hyperlink"] = hyperlink_when.value


app.add_typer(passphrase_app, name="passphrase")
app.add_typer(diffusion_app, name="diffusion")
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
