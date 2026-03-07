# -*- coding: utf-8 -*-
"""Typer-based CLI for phabfive."""

import os
import re
import sys
from importlib.metadata import version
from typing import Optional

# Disable Typer's Rich formatting for help text to remove fancy boxes
os.environ.setdefault("TYPER_USE_RICH", "0")

import typer  # noqa: E402

from phabfive.cli.diffusion import diffusion_app  # noqa: E402
from phabfive.cli.maniphest import maniphest_app  # noqa: E402
from phabfive.cli.passphrase import passphrase_app  # noqa: E402
from phabfive.cli.paste import paste_app  # noqa: E402
from phabfive.cli.repl import repl_app  # noqa: E402
from phabfive.cli.user import user_app  # noqa: E402
from phabfive.constants import COMMENTS_SUPPORTED, MONOGRAM_SHORTCUT, MONOGRAMS  # noqa: E402

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


def preprocess_monograms(argv: list[str]) -> list[str]:
    """Expand monogram shortcuts in argv before Typer parsing.

    Examples:
        T123 → maniphest show T123
        T123 'comment' → maniphest comment T123 'comment'
        K123 → passphrase K123
        P123 → paste show P123
        R123 → diffusion branch list R123
    """
    if len(argv) < 2:
        return argv

    first_arg = argv[1]
    match = _MONOGRAM_PATTERN.match(first_arg)

    if match:
        prefix = match.group(1)
        expansion = MONOGRAM_SHORTCUT[prefix]

        # Handle comment shortcut: T123 'text' → maniphest comment T123 'text'
        if (
            prefix in _COMMENT_PREFIXES
            and len(argv) > 2
            and not argv[2].startswith("-")
        ):
            app_name = expansion[0]  # e.g., 'maniphest'
            return [argv[0], app_name, "comment", first_arg] + argv[2:]

        return [argv[0]] + expansion + [first_arg] + argv[2:]

    return argv


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        typer.echo(version("phabfive"))
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Set log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    ),
    output_format: Optional[str] = typer.Option(
        None,
        "--format",
        help="Output format: rich, tree, or strict. Auto-detects based on TTY.",
    ),
    ascii_when: str = typer.Option(
        "auto",
        "--ascii",
        help="Use ASCII instead of Unicode (always/auto/never)",
    ),
    hyperlink_when: str = typer.Option(
        "auto",
        "--hyperlink",
        help="Enable terminal hyperlinks (always/auto/never)",
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
    ctx.obj["log_level"] = log_level
    ctx.obj["format"] = output_format
    ctx.obj["ascii"] = ascii_when
    ctx.obj["hyperlink"] = hyperlink_when


app.add_typer(passphrase_app, name="passphrase")
app.add_typer(diffusion_app, name="diffusion")
app.add_typer(paste_app, name="paste")
app.add_typer(user_app, name="user")
app.add_typer(maniphest_app, name="maniphest")
app.add_typer(repl_app, name="repl")


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

    # Preprocess monograms before Typer sees the args
    sys.argv = preprocess_monograms(sys.argv)

    try:
        app()
    except KeyboardInterrupt:
        raise typer.Exit(130)
