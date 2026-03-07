# -*- coding: utf-8 -*-
"""Paste commands for phabfive CLI."""

from typing import List, Optional

import typer

from phabfive.exceptions import PhabfiveConfigException

paste_app = typer.Typer(help="The paste app")


def _get_paste_app():
    """Get Paste app instance with config error handling."""
    from phabfive.paste import Paste

    try:
        return Paste()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Paste()


@paste_app.command("list")
def paste_list(ctx: typer.Context) -> None:
    """List all pastes."""
    paste = _get_paste_app()
    pastes = paste.get_pastes_formatted()
    for p in pastes:
        typer.echo(f"{p['id']} {p['title']}")


@paste_app.command()
def create(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="Title for Paste"),
    file: str = typer.Argument(..., help="A file with text content for Paste"),
    tags: Optional[str] = typer.Option(
        None,
        "--tags",
        "-t",
        help="Project name(s), comma-separated (e.g., projectX,projectY)",
    ),
    subscribers: Optional[str] = typer.Option(
        None,
        "--subscribers",
        "-s",
        help="Subscriber names, comma-separated (e.g., user1,user2)",
    ),
) -> None:
    """Create a new paste from a file."""
    paste = _get_paste_app()

    tags_list = tags.split(",") if tags else None
    subscribers_list = subscribers.split(",") if subscribers else None

    paste.create_paste(
        title=title,
        file=file,
        tags=tags_list,
        subscribers=subscribers_list,
    )


@paste_app.command()
def show(
    ctx: typer.Context,
    ids: List[str] = typer.Argument(..., help="Paste monogram(s) (e.g., P1 P2 P3)"),
) -> None:
    """Show specific pastes by ID."""
    paste = _get_paste_app()
    pastes = paste.get_pastes_formatted(ids=ids)
    for p in pastes:
        typer.echo(f"{p['id']} {p['title']}")
