# -*- coding: utf-8 -*-
"""Shared output-format helpers for the phabfive CLI.

Every CLI module needs the same two things: the format the user asked for on
the root command (or an auto-detected one when they asked for nothing), and the
global ``Phabfive`` output options set from the same context. That pair used to
be copy-pasted into ``maniphest``, ``paste``, ``user``, ``cache`` and
``passphrase``, where it drifted. It lives here so that the next app - starting
with ``diffusion``, which has no format handling yet (#371, #372, #376) - imports
it instead of writing a sixth copy.
"""

import typer

# Re-exported: every CLI module imports them from here.
from phabfive.constants import MACHINE_FORMATS, is_machine_format  # noqa: F401


def _get_output_format(ctx: typer.Context) -> str:
    """Get the output format from context, or auto-detect it.

    ``ctx.obj["format"]`` is the ``--format`` value the root command stored, or
    None when the user gave no ``--format``. A missing or empty value means
    "decide from the terminal", which is what ``_get_auto_format()`` does.
    """
    from phabfive.core import Phabfive

    format_arg = ctx.obj.get("format") if ctx.obj else None
    if format_arg:
        return format_arg
    return Phabfive._get_auto_format()


def _setup_output_options(ctx: typer.Context) -> None:
    """Set the global output options from context."""
    from phabfive.core import Phabfive

    if ctx.obj:
        ascii_when = ctx.obj.get("ascii", "auto")
        hyperlink_when = ctx.obj.get("hyperlink", "auto")
        output_format = _get_output_format(ctx)

        Phabfive.set_output_options(
            ascii_when=ascii_when,
            hyperlink_when=hyperlink_when,
            output_format=output_format,
        )


def _echo_no_match_hint(query) -> None:
    """Explain Phorge's search operators after a text search found nothing.

    On stderr, so a program reading stdout still sees an empty result.
    """
    from phabfive.fulltext import no_match_hint

    hint = no_match_hint(query)
    if hint:
        typer.echo(hint, err=True)
