# -*- coding: utf-8 -*-
"""Paste commands for phabfive CLI."""

import re
import sys
from datetime import datetime
from typing import List, Optional

import typer
import yaml

from phabfive.constants import MONOGRAMS
from phabfive.exceptions import PhabfiveConfigException

paste_app = typer.Typer(help="The paste app")


def _get_output_format(ctx: typer.Context):
    """Get output format from context or auto-detect."""
    from phabfive.core import Phabfive

    format_arg = ctx.obj.get("format") if ctx.obj else None
    if format_arg is None:
        return Phabfive._get_auto_format()
    return format_arg


def _setup_output_options(ctx: typer.Context):
    """Set up output options from context."""
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


def _get_paste_app():
    """Get Paste app instance with config error handling."""
    import requests

    from phabfive.paste import Paste

    try:
        return Paste()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Paste()
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


@paste_app.command("list")
def paste_list(ctx: typer.Context) -> None:
    """List all pastes (alias for search)."""
    # Delegate to search with no filters
    ctx.invoke(search)


@paste_app.command()
def search(
    ctx: typer.Context,
    author: Optional[str] = typer.Option(
        None, "--author", help="Filter by author (username or @me)"
    ),
) -> None:
    """Search and list pastes with optional filters.

    \b
    Examples:
        phabfive paste search
        phabfive paste search "script"
        phabfive paste search --author=@me
        phabfive paste search "config" --author=@me
        phabfive --format=yaml paste search
    """
    _setup_output_options(ctx)
    paste = _get_paste_app()

    # Build constraints
    constraints = {}

    # Handle @me shortcut for author
    if author:
        if author == "@me":
            whoami = paste.phab.user.whoami()
            author_phid = whoami.get("phid")
        else:
            # Look up user by username
            users = paste.phab.user.search(constraints={"usernames": [author]})
            if users.get("data"):
                author_phid = users["data"][0]["phid"]
            else:
                sys.stderr.write(f"Error: User '{author}' not found\n")
                raise typer.Exit(1)
        constraints["authorPHIDs"] = [author_phid]

    # Get pastes with constraints
    pastes = paste.get_pastes(constraints=constraints if constraints else None)

    if not pastes:
        typer.echo("No pastes found")
        return

    # Format output
    output_format = _get_output_format(ctx)

    if output_format == "json":
        import json

        result = [
            {"id": f"P{p['id']}", "title": p["fields"]["title"]} for p in pastes
        ]
        print(json.dumps(result, indent=2))
    elif output_format in ("yaml", "strict"):
        result = [
            {"id": f"P{p['id']}", "title": p["fields"]["title"]} for p in pastes
        ]
        print(yaml.dump(result, default_flow_style=False, allow_unicode=True))
    else:
        # Rich/default format
        for p in pastes:
            typer.echo(f"P{p['id']} {p['fields']['title']}")


@paste_app.command()
def create(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="Title for Paste"),
    file: Optional[str] = typer.Argument(
        None, help="File with content (optional if using --content or $EDITOR)"
    ),
    content: Optional[str] = typer.Option(
        None,
        "--content",
        help="Paste content (use - to read from stdin, omit for $EDITOR)",
    ),
    language: Optional[str] = typer.Option(
        None, "--language", "-l", help="Language for syntax highlighting"
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", "-t", help="Add to project (repeatable)"
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None, "--subscribe", "-s", help="Add subscriber (username or @me, repeatable)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without creating"),
) -> None:
    """Create a new paste.

    \b
    Examples:
        phabfive paste create "My Script" script.py
        phabfive paste create "Notes" --content="Some text"
        echo "content" | phabfive paste create "From stdin" --content=-
        phabfive paste create "Code" --language=python  # opens $EDITOR
        phabfive paste create "Task" --subscribe=@me --tag=project
    """
    paste = _get_paste_app()

    # Determine content source
    final_content = None

    if file:
        # Read from file
        try:
            with open(file, "r") as f:
                final_content = f.read()
        except FileNotFoundError:
            sys.stderr.write(f"Error: File not found: {file}\n")
            raise typer.Exit(1)
        except IOError as e:
            sys.stderr.write(f"Error reading file: {e}\n")
            raise typer.Exit(1)

        # Auto-detect language from file extension if not specified
        if not language and "." in file:
            ext = file.rsplit(".", 1)[-1].lower()
            ext_map = {
                "py": "python",
                "js": "javascript",
                "ts": "typescript",
                "sh": "bash",
                "bash": "bash",
                "json": "json",
                "yaml": "yaml",
                "yml": "yaml",
                "sql": "sql",
                "html": "html",
                "css": "css",
                "c": "c",
                "cpp": "cpp",
                "h": "c",
                "java": "java",
                "go": "go",
                "rs": "rust",
                "rb": "ruby",
                "php": "php",
                "md": "text",
                "txt": "text",
            }
            language = ext_map.get(ext)

    elif content == "-":
        # Read from stdin
        if sys.stdin.isatty():
            sys.stderr.write("Error: --content=- requires input from stdin\n")
            raise typer.Exit(1)
        final_content = sys.stdin.read()

    elif content is not None:
        # Use provided content directly
        final_content = content

    elif sys.stdin.isatty() and not dry_run:
        # Open $EDITOR
        from phabfive.editor import edit_text

        suffix = f".{language}" if language else ".txt"
        final_content = edit_text("", prefix="paste-", suffix=suffix)
        if final_content is None:
            print("Paste creation cancelled")
            raise typer.Exit(0)

    else:
        sys.stderr.write(
            "Error: No content provided. Use a file, --content, or run interactively.\n"
        )
        raise typer.Exit(1)

    # Handle @me shortcut for subscribers
    subscriber_names = []
    if subscribe:
        for sub in subscribe:
            if sub == "@me":
                whoami = paste.phab.user.whoami()
                subscriber_names.append(whoami.get("userName", sub))
            else:
                subscriber_names.append(sub)

    # Handle tags
    tag_list = list(tag) if tag else None

    if dry_run:
        print("[DRY RUN] Would create paste:")
        print(f"  Title: {title}")
        if language:
            print(f"  Language: {language}")
        if tag_list:
            print(f"  Tags: {', '.join(tag_list)}")
        if subscriber_names:
            print(f"  Subscribers: {', '.join(subscriber_names)}")
        # Show content preview
        lines = final_content.split("\n")
        if len(lines) <= 5:
            print("  Content:")
            for line in lines:
                print(f"    {line}")
        else:
            print(f"  Content: ({len(lines)} lines, {len(final_content)} chars)")
        raise typer.Exit(0)

    # Create the paste
    result = paste.create_paste_from_content(
        title=title,
        content=final_content,
        language=language,
        tags=tag_list,
        subscribers=subscriber_names,
    )

    # Output the result
    print(f"P{result['id']}")


@paste_app.command()
def show(
    ctx: typer.Context,
    paste_ids: List[str] = typer.Argument(..., help="Paste monogram(s) (e.g., P1 P2)"),
    show_content: bool = typer.Option(
        True, "--show-content/--no-content", help="Show paste content"
    ),
) -> None:
    """Show details for one or more pastes.

    \b
    Examples:
        phabfive paste show P1
        phabfive paste show P1 P2 --no-content
        phabfive --format=yaml paste show P1
    """
    _setup_output_options(ctx)
    paste = _get_paste_app()

    # Validate all paste ID formats
    paste_pattern = f"^{MONOGRAMS['paste']}$"
    ids = []
    for paste_id in paste_ids:
        if not re.match(paste_pattern, paste_id):
            typer.echo(
                f"Invalid paste ID '{paste_id}'. Expected format: P123", err=True
            )
            raise typer.Exit(1)
        ids.append(int(paste_id[1:]))

    result = paste.paste_show(ids, show_content=show_content)

    output_format = _get_output_format(ctx)
    _display_pastes(result, output_format, paste)


def _display_pastes(result, output_format, paste_instance):
    """Display paste results in the specified format."""
    import json

    from rich.syntax import Syntax

    if not result or not result.get("pastes"):
        return

    pastes = result["pastes"]

    if output_format == "json":
        print(json.dumps(pastes, indent=2, default=str))
    elif output_format in ("yaml", "strict"):
        print(yaml.dump(pastes, default_flow_style=False, allow_unicode=True))
    else:
        # Rich format
        console = paste_instance.get_console()

        for paste_data in pastes:
            # Build header
            title = f"{paste_data['id']}: {paste_data['title']}"

            # Build metadata lines
            lines = []
            if paste_data.get("author"):
                lines.append(f"Author: {paste_data['author']}")
            if paste_data.get("language"):
                lines.append(f"Language: {paste_data['language']}")
            if paste_data.get("dateCreated"):
                created = datetime.fromtimestamp(paste_data["dateCreated"])
                lines.append(f"Created: {created.strftime('%Y-%m-%d %H:%M')}")

            # Print header and metadata
            console.print(f"[bold]{title}[/bold]")
            console.print("─" * min(len(title) + 10, 60))
            for line in lines:
                console.print(line)

            # Print content with syntax highlighting
            if paste_data.get("content"):
                console.print()
                language = paste_data.get("language", "text")
                # Map common phabricator language names to pygments lexers
                lang_map = {
                    "text": "text",
                    "python": "python",
                    "python3": "python",
                    "javascript": "javascript",
                    "js": "javascript",
                    "bash": "bash",
                    "shell": "bash",
                    "json": "json",
                    "yaml": "yaml",
                    "sql": "sql",
                    "html": "html",
                    "css": "css",
                    "c": "c",
                    "cpp": "cpp",
                    "java": "java",
                    "go": "go",
                    "rust": "rust",
                    "ruby": "ruby",
                    "php": "php",
                }
                lexer = lang_map.get(language.lower(), "text")
                try:
                    syntax = Syntax(
                        paste_data["content"],
                        lexer,
                        theme="monokai",
                        line_numbers=True,
                    )
                    console.print(syntax)
                except Exception:
                    # Fall back to plain text if syntax highlighting fails
                    console.print(paste_data["content"])

            console.print()
