# -*- coding: utf-8 -*-
"""Paste commands for phabfive CLI."""

import re
import sys
from datetime import datetime
from typing import List, Optional

import typer
from io import StringIO

from rich.text import Text
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.cli.completers import complete_language
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


@paste_app.command()
def search(
    ctx: typer.Context,
    text_query: Optional[str] = typer.Argument(
        None, help="Free-text search in paste title"
    ),
    author: Optional[str] = typer.Option(
        None, "--author", help="Filter by author (username or @me)"
    ),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum results to return"),
) -> None:
    """Search and list pastes with optional filters.

    \b
    Examples:
        phabfive paste search "script"
        phabfive paste search --author=@me
        phabfive paste search "config" --author=@me
        phabfive --format=yaml paste search "notes"
    """
    # Require at least one search criterion
    if not text_query and not author:
        typer.echo("Usage:")
        typer.echo("    phabfive paste search [<text_query>] [options]")
        return

    _setup_output_options(ctx)
    paste = _get_paste_app()

    # Build constraints
    constraints = {}

    # Handle free-text query
    if text_query:
        constraints["query"] = text_query

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
    pastes = paste.get_pastes(
        constraints=constraints if constraints else None, limit=limit
    )

    if not pastes:
        typer.echo("No pastes found")
        return

    # Format output
    output_format = _get_output_format(ctx)

    if output_format == "json":
        import json

        result = [{"id": f"P{p['id']}", "title": p["fields"]["title"]} for p in pastes]
        print(json.dumps(result, indent=2))
    elif output_format in ("yaml", "strict"):
        result = [{"id": f"P{p['id']}", "title": p["fields"]["title"]} for p in pastes]
        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(result, stream)
        print(stream.getvalue(), end="")
    else:
        # Rich/default format
        for p in pastes:
            typer.echo(f"P{p['id']} {p['fields']['title']}")


@paste_app.command()
def create(
    ctx: typer.Context,
    title: Optional[str] = typer.Argument(None, help="Title for Paste"),
    file: Optional[str] = typer.Argument(
        None, help="File with content (optional if using --content or $EDITOR)"
    ),
    title_opt: Optional[str] = typer.Option(
        None,
        "--title",
        hidden=True,
        help="Title for Paste (hidden, use positional argument instead)",
    ),
    content: Optional[str] = typer.Option(
        None,
        "--content",
        help="Paste content (use - to read from stdin, omit for $EDITOR)",
    ),
    language: Optional[str] = typer.Option(
        None,
        "--language",
        help="Language for syntax highlighting",
        autocompletion=complete_language,
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", help="Add to project (repeatable)"
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None, "--subscribe", help="Add subscriber (username or @me, repeatable)"
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
        phabfive paste create "Notes" --subscribe=@me --tag=project
    """
    paste = _get_paste_app()

    # Merge positional and option title (positional takes precedence)
    final_title = title or title_opt
    if not final_title:
        sys.stderr.write("Error: Title is required\n")
        raise typer.Exit(1)

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
        print(f"  Title: {final_title}")
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
        title=final_title,
        content=final_content,
        language=language,
        tags=tag_list,
        subscribers=subscriber_names,
    )

    # Output the result (full URL, consistent with maniphest create)
    print(paste.get_paste_url(result["id"]))


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


def _format_timestamp(ts):
    """Convert Unix timestamp to ISO format string."""
    if ts:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    return None


def _display_pastes(result, output_format, paste_instance):
    """Display paste results in the specified format."""
    import json

    if not result or not result.get("pastes"):
        return

    pastes = result["pastes"]

    if output_format == "json":
        # Use capitalized keys like passphrase/maniphest
        output = []
        for p in pastes:
            item = {
                "Link": p.get("url", ""),
                "Title": p.get("title", ""),
                "Author": p.get("author", ""),
                "Language": p.get("language", "text"),
                "Status": p.get("status", ""),
                "Created": _format_timestamp(p.get("dateCreated")),
                "Modified": _format_timestamp(p.get("dateModified")),
            }
            if "content" in p:
                item["Content"] = p.get("content", "")
            output.append(item)
        print(json.dumps(output, indent=2, default=str))
    elif output_format in ("yaml", "strict"):
        yaml = YAML()
        yaml.default_flow_style = False
        # Use capitalized keys like passphrase/maniphest
        output = []
        for p in pastes:
            item = {
                "Link": p.get("url", ""),
                "Title": p.get("title", ""),
                "Author": p.get("author", ""),
                "Language": p.get("language", "text"),
                "Status": p.get("status", ""),
                "Created": _format_timestamp(p.get("dateCreated")),
                "Modified": _format_timestamp(p.get("dateModified")),
            }
            if "content" in p:
                content = p.get("content", "")
                if "\n" in content:
                    item["Content"] = PreservedScalarString(content)
                else:
                    item["Content"] = content
            output.append(item)
        stream = StringIO()
        yaml.dump(output, stream)
        print(stream.getvalue(), end="")
    elif output_format == "tree":
        from rich.tree import Tree

        console = paste_instance.get_console()
        for paste_data in pastes:
            # Use URL as tree root (like passphrase/maniphest)
            tree = Tree(paste_data.get("url", paste_data["id"]))
            tree.add(f"Title: {paste_data.get('title', '')}")
            if paste_data.get("author"):
                tree.add(f"Author: {paste_data['author']}")
            if paste_data.get("language"):
                tree.add(f"Language: {paste_data['language']}")
            if paste_data.get("status"):
                tree.add(f"Status: {paste_data['status']}")
            created = _format_timestamp(paste_data.get("dateCreated"))
            if created:
                tree.add(f"Created: {created}")
            modified = _format_timestamp(paste_data.get("dateModified"))
            if modified:
                tree.add(f"Modified: {modified}")
            if paste_data.get("content"):
                # Show content preview for tree view
                content = paste_data["content"]
                if "\n" in content:
                    content_branch = tree.add("Content:")
                    for line in content.splitlines()[:5]:
                        content_branch.add(line)
                    if len(content.splitlines()) > 5:
                        content_branch.add("...")
                else:
                    tree.add(
                        f"Content: {content[:100]}{'...' if len(content) > 100 else ''}"
                    )
            console.print(tree)
    elif output_format == "simple":
        # Just output content for piping (like passphrase outputs secret)
        for paste_data in pastes:
            if paste_data.get("content"):
                print(paste_data["content"])
    else:
        # Rich format - YAML-like output with hyperlinks
        console = paste_instance.get_console()

        for paste_data in pastes:
            link = paste_data.get("_link")

            # Print link
            console.print(Text.assemble("- Link: ", link))

            # Print Title
            console.print(f"  Title: {paste_data.get('title', '')}")

            # Print Author (only when present)
            if paste_data.get("author"):
                console.print(f"  Author: {paste_data['author']}")

            # Print Language
            if paste_data.get("language"):
                console.print(f"  Language: {paste_data['language']}")

            # Print Status
            if paste_data.get("status"):
                console.print(f"  Status: {paste_data['status']}")

            # Print dates
            created = _format_timestamp(paste_data.get("dateCreated"))
            if created:
                console.print(f"  Created: {created}")
            modified = _format_timestamp(paste_data.get("dateModified"))
            if modified:
                console.print(f"  Modified: {modified}")

            # Print Content (only when present and non-empty)
            content = paste_data.get("content", "")
            if content:
                if "\n" in content:
                    console.print("  Content: |-")
                    for line in content.splitlines():
                        console.print(f"    {line}")
                else:
                    console.print(f"  Content: {content}")


@paste_app.command()
def edit(
    ctx: typer.Context,
    paste_id: str = typer.Argument(..., help="Paste monogram (e.g., P123)"),
    title: Optional[str] = typer.Argument(None, help="New title for the paste"),
    title_opt: Optional[str] = typer.Option(
        None,
        "--title",
        hidden=True,
        help="New title (hidden, use positional argument instead)",
    ),
    content: Optional[str] = typer.Option(
        None,
        "--content",
        help="New content (use - for stdin, omit to open $EDITOR with current content)",
    ),
    language: Optional[str] = typer.Option(
        None,
        "--language",
        help="Language for syntax highlighting",
        autocompletion=complete_language,
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", help="Add to project (repeatable)"
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None, "--subscribe", help="Add subscriber (username or @me, repeatable)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview changes without applying"
    ),
    force: bool = typer.Option(
        False, "--force", help="Apply changes without confirmation"
    ),
) -> None:
    """Edit an existing paste.

    \b
    Examples:
        phabfive paste edit P1 "New Title"
        phabfive paste edit P1 --language=python
        phabfive paste edit P1 --content="Updated content"
        echo "new content" | phabfive paste edit P1 --content=-
        phabfive paste edit P1 --content  # opens $EDITOR with current content
        phabfive paste edit P1 --subscribe=@me --tag=project
        phabfive paste edit P1 "Test" --dry-run
    """
    from phabfive.editor import confirm_text_change, edit_text

    _setup_output_options(ctx)
    paste = _get_paste_app()

    # Merge positional and option title (positional takes precedence)
    final_title = title or title_opt

    # Validate paste ID format
    paste_pattern = f"^{MONOGRAMS['paste']}$"
    if not re.match(paste_pattern, paste_id):
        sys.stderr.write(
            f"Error: Invalid paste ID '{paste_id}'. Expected format: P123\n"
        )
        raise typer.Exit(1)

    numeric_id = int(paste_id[1:])

    # Get current paste data
    try:
        current_paste = paste.get_paste_data(numeric_id)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    # Handle content editing
    final_content = None
    if content == "-":
        # Read from stdin
        if sys.stdin.isatty():
            sys.stderr.write("Error: --content=- requires input from stdin\n")
            raise typer.Exit(1)
        final_content = sys.stdin.read()
    elif content == "" or (content is None and "--content" in sys.argv):
        # Open $EDITOR with current content
        new_content = edit_text(
            current_paste["content"],
            prefix=f"paste-{paste_id}-",
            suffix=f".{current_paste['language']}",
        )
        if new_content is None:
            print("Edit cancelled (no changes)")
            raise typer.Exit(0)
        final_content = new_content
    elif content is not None:
        final_content = content

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

    # Show diff for content changes
    if final_content is not None and not dry_run:
        confirmed, return_code = confirm_text_change(
            current_paste["content"], final_content, force
        )
        if not confirmed:
            raise typer.Exit(return_code or 0)

    # Show diff for title changes
    if final_title is not None and not dry_run:
        current_title = current_paste.get("title", "")
        if final_title != current_title:
            confirmed, return_code = confirm_text_change(
                current_title, final_title, force, filename="title"
            )
            if not confirmed:
                raise typer.Exit(return_code or 0)

    # Perform edit
    result = paste.edit_paste(
        paste_id=numeric_id,
        title=final_title,
        content=final_content,
        language=language,
        tags=tag_list,
        subscribers=subscriber_names if subscriber_names else None,
        dry_run=dry_run,
    )

    # Output result
    if dry_run:
        from phabfive.editor import show_diff

        print(f"[DRY RUN] Would edit {paste_id}:")
        for change in result.get("changes", []):
            if change["field"] == "Title":
                # Show unified diff for title
                print()
                show_diff(current_paste.get("title", ""), final_title, filename="title")
            else:
                print(f"  {change['field']}: {change['new']}")
        if not result.get("changes"):
            print("  No changes specified")
    else:
        if result.get("changes"):
            print(f"Updated {paste_id}")
            for change in result["changes"]:
                print(f"  {change['field']}: {change['new']}")
        else:
            print(result.get("message", "No changes made"))


@paste_app.command()
def comment(
    ctx: typer.Context,
    paste_id: str = typer.Argument(..., help="Paste monogram (e.g., P123)"),
    text: Optional[str] = typer.Argument(
        None, help="Comment text (omit to open $EDITOR)"
    ),
) -> None:
    """Add a comment to a paste.

    \b
    Examples:
        phabfive paste comment P1 "Great paste!"
        phabfive paste comment P1  # opens $EDITOR
        echo "comment" | phabfive paste comment P1 -
        phabfive P1 "Quick comment"  # monogram shortcut
    """
    from phabfive.editor import edit_text

    paste = _get_paste_app()

    # Validate paste ID format
    paste_pattern = f"^{MONOGRAMS['paste']}$"
    if not re.match(paste_pattern, paste_id):
        sys.stderr.write(
            f"Error: Invalid paste ID '{paste_id}'. Expected format: P123\n"
        )
        raise typer.Exit(1)

    numeric_id = int(paste_id[1:])

    # Determine comment text
    final_text = None
    if text == "-":
        # Read from stdin
        if sys.stdin.isatty():
            sys.stderr.write("Error: '-' requires input from stdin\n")
            raise typer.Exit(1)
        final_text = sys.stdin.read().strip()
    elif text is not None:
        final_text = text
    else:
        # Open $EDITOR
        if not sys.stdin.isatty():
            sys.stderr.write("Error: Provide comment text or run interactively\n")
            raise typer.Exit(1)
        final_text = edit_text("", prefix="paste-comment-", suffix=".remarkup")
        if final_text is None:
            print("Comment cancelled")
            raise typer.Exit(0)

    if not final_text:
        sys.stderr.write("Error: Comment cannot be empty\n")
        raise typer.Exit(1)

    # Add the comment
    try:
        paste.add_paste_comment(numeric_id, final_text)
        print(paste.get_paste_url(numeric_id))
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)
