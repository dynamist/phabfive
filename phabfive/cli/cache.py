# -*- coding: utf-8 -*-
"""Cache commands for phabfive CLI."""

import typer

from phabfive import cache
from phabfive.core import Phabfive

cache_app = typer.Typer(help="Inspect and clear cached completion data")


def _get_output_format(ctx: typer.Context) -> str:
    """Get the output format from context or auto-detect."""
    format_arg = ctx.obj.get("format") if ctx.obj else None
    if format_arg:
        return format_arg
    return Phabfive._get_auto_format()


def _human_size(size: int) -> str:
    """Render a byte count the way a person reads it."""
    for unit in ["B", "KB", "MB"]:
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _human_age(seconds: int) -> str:
    """Render an age as the largest unit that still says something."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


@cache_app.command()
def clear(
    all_instances: bool = typer.Option(
        False,
        "--all",
        help="Clear every instance, not just the configured one.",
    ),
) -> None:
    """Remove cached completion data.

    Completion data is advisory, so clearing it only costs one slower TAB.
    Use this after somebody joins, leaves or is renamed, rather than waiting
    for the entry to expire.
    """
    removed = cache.clear(all_instances=all_instances)

    if removed is None:
        typer.echo(
            "No instance is configured, so there is nothing to clear. "
            "Use --all to clear every cached instance.",
            err=True,
        )
        raise typer.Exit(1)

    where = "every instance" if all_instances else "the configured instance"
    typer.echo(f"Removed {removed} cached {_entries(removed)} from {where}")


def _entries(count: int) -> str:
    return "entry" if count == 1 else "entries"


@cache_app.command()
def info(ctx: typer.Context) -> None:
    """Show where completion data is cached and how much of it there is.

    Reports sizes and ages only - cached values are never printed.
    """
    described = cache.describe()
    output_format = _get_output_format(ctx)

    if output_format == "json":
        import json

        print(json.dumps(described, indent=2))
        return

    if output_format in ("yaml", "strict"):
        from io import StringIO

        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(described, stream)
        print(stream.getvalue(), end="")
        return

    _display_rich(described)


def _display_rich(described: dict) -> None:
    """Show the cache summary as a table."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    state = "enabled" if described["Enabled"] else f"off ({described['Reason']})"
    console.print(f"Cache: {state}")
    console.print(f"Path:  {described['Path']}")

    namespaces = described["Namespaces"]
    if not namespaces:
        console.print("\nNothing cached yet.")
        return

    table = Table(show_header=True, header_style="bold")
    for column in ["Namespace", "Entries", "Size", "TTL", "Oldest", "Newest"]:
        table.add_column(column)

    for namespace in namespaces:
        table.add_row(
            namespace["Namespace"],
            str(namespace["Entries"]),
            _human_size(namespace["Size"]),
            _human_age(namespace["TTL"]),
            _human_age(namespace["Oldest"]),
            _human_age(namespace["Newest"]),
        )

    console.print()
    console.print(table)
