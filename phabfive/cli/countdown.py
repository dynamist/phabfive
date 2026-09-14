# -*- coding: utf-8 -*-
"""Countdown commands for phabfive CLI."""

import re
import sys
from datetime import datetime
from io import StringIO
from typing import List, Optional

import typer
from rich.text import Text
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.constants import MONOGRAMS
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException

countdown_app = typer.Typer(help="The countdown app")


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


def _get_countdown_app():
    """Get Countdown app instance with config error handling."""
    import requests

    from phabfive.countdown import Countdown

    try:
        return Countdown()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Countdown()
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


def _format_timestamp(ts):
    """Convert Unix timestamp to ISO format string."""
    if ts:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    return None


def _display_countdowns(result, output_format, countdown_instance):
    """Display countdown results in the specified format (matching maniphest UX)."""
    import json

    if not result or not result.get("countdowns"):
        return

    countdowns = result["countdowns"]

    if output_format == "json":
        output = []
        for c in countdowns:
            countdown_fields = c.get("Countdown", {})
            item = {
                "Link": c.get("_url", ""),
                "Countdown": countdown_fields,
            }
            if c.get("_author"):
                item["Countdown"]["Author"] = c.get("_author")
            output.append(item)
        print(json.dumps(output, indent=2))

    elif output_format in ("yaml", "strict"):
        yaml = YAML()
        yaml.default_flow_style = False
        output = []
        for c in countdowns:
            countdown_fields = dict(c.get("Countdown", {}))
            # Handle multiline description
            if "Description" in countdown_fields:
                desc = countdown_fields["Description"]
                if "\n" in desc:
                    countdown_fields["Description"] = PreservedScalarString(desc)
            if c.get("_author"):
                countdown_fields["Author"] = c.get("_author")
            item = {
                "Link": c.get("_url", ""),
                "Countdown": countdown_fields,
            }
            output.append(item)
        stream = StringIO()
        yaml.dump(output, stream)
        print(stream.getvalue(), end="")

    elif output_format == "tree":
        from rich.tree import Tree

        console = countdown_instance.get_console()
        for c in countdowns:
            countdown_fields = c.get("Countdown", {})
            tree = Tree(c.get("_url", ""))
            countdown_branch = tree.add("Countdown:")
            for key, value in countdown_fields.items():
                if key == "Description" and value and "\n" in value:
                    desc_branch = countdown_branch.add("Description:")
                    for line in value.splitlines()[:5]:
                        desc_branch.add(line)
                    if len(value.splitlines()) > 5:
                        desc_branch.add("...")
                else:
                    countdown_branch.add(f"{key}: {value}")
            if c.get("_author"):
                countdown_branch.add(f"Author: {c.get('_author')}")
            console.print(tree)

    else:
        # Rich format - YAML-like output with hyperlinks (matching maniphest)
        console = countdown_instance.get_console()

        for c in countdowns:
            link = c.get("_link")
            countdown_fields = c.get("Countdown", {})

            # Print link
            console.print(Text.assemble("- Link: ", link))

            # Print Countdown section (like maniphest Task section)
            console.print("  Countdown:")
            for key, value in countdown_fields.items():
                if key == "Description" and value and "\n" in str(value):
                    # Multi-line value
                    console.print(f"    {key}: |-")
                    for line in str(value).splitlines():
                        console.print(f"      {line}")
                else:
                    console.print(f"    {key}: {value}")

            # Print Author (like maniphest Assignee)
            if c.get("_author"):
                console.print(f"    Author: {c.get('_author')}")


@countdown_app.command()
def show(
    ctx: typer.Context,
    countdown_ids: List[str] = typer.Argument(
        ..., help="Countdown monogram(s) (e.g., C1 C2)"
    ),
) -> None:
    """Show details for one or more countdowns.

    \b
    Examples:
        phabfive countdown show C1
        phabfive countdown show C1 C2
        phabfive --format=yaml countdown show C1
        phabfive C1  # monogram shortcut
    """
    _setup_output_options(ctx)
    countdown = _get_countdown_app()

    # Validate all countdown ID formats
    countdown_pattern = f"^{MONOGRAMS['countdown']}$"
    ids = []
    for countdown_id in countdown_ids:
        if not re.match(countdown_pattern, countdown_id):
            typer.echo(
                f"Invalid countdown ID '{countdown_id}'. Expected format: C123",
                err=True,
            )
            raise typer.Exit(1)
        ids.append(int(countdown_id[1:]))

    result = countdown.countdown_show(ids)

    output_format = _get_output_format(ctx)
    _display_countdowns(result, output_format, countdown)


@countdown_app.command()
def search(
    ctx: typer.Context,
    text_query: Optional[str] = typer.Argument(
        None, help="Free-text search in countdown title"
    ),
    author: Optional[str] = typer.Option(
        None, "--author", help="Filter by author (username or @me)"
    ),
) -> None:
    """Search and list countdowns with optional filters.

    \b
    Examples:
        phabfive countdown search "launch"
        phabfive countdown search --author=@me
        phabfive --format=yaml countdown search "release"
    """
    if not text_query and not author:
        typer.echo("Usage:")
        typer.echo("    phabfive countdown search [<text_query>] [options]")
        return

    _setup_output_options(ctx)
    countdown = _get_countdown_app()

    constraints = {}

    if text_query:
        constraints["query"] = text_query

    if author:
        if author == "@me":
            whoami = countdown.phab.user.whoami()
            author_phid = whoami.get("phid")
        else:
            users = countdown.phab.user.search(constraints={"usernames": [author]})
            if users.get("data"):
                author_phid = users["data"][0]["phid"]
            else:
                sys.stderr.write(f"Error: User '{author}' not found\n")
                raise typer.Exit(1)
        constraints["authorPHIDs"] = [author_phid]

    countdowns = countdown.get_countdowns(
        constraints=constraints if constraints else None
    )

    if not countdowns:
        typer.echo("No countdowns found")
        return

    output_format = _get_output_format(ctx)

    if output_format == "json":
        import json

        result = [
            {"id": f"C{c['id']}", "title": c["fields"]["name"]} for c in countdowns
        ]
        print(json.dumps(result, indent=2))
    elif output_format in ("yaml", "strict"):
        result = [
            {"id": f"C{c['id']}", "title": c["fields"]["name"]} for c in countdowns
        ]
        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(result, stream)
        print(stream.getvalue(), end="")
    else:
        for c in countdowns:
            typer.echo(f"C{c['id']} {c['fields']['name']}")


@countdown_app.command()
def create(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="Title for the countdown"),
    epoch: str = typer.Argument(
        ..., help="Target date (ISO 8601, +7d relative, or Unix timestamp)"
    ),
    description: Optional[str] = typer.Option(
        None, "--description", help="Countdown description"
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", help="Add to project (repeatable)"
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None, "--subscribe", help="Add subscriber (username or @me, repeatable)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without creating"),
) -> None:
    """Create a new countdown.

    \b
    Epoch format examples:
        2026-06-15T10:00:00    # ISO 8601
        2026-06-15             # Date only (midnight)
        +7d                    # 7 days from now
        +2w                    # 2 weeks from now
        +1m                    # 1 month (30 days) from now
        +4h                    # 4 hours from now

    \b
    Examples:
        phabfive countdown create "Product Launch" "2026-06-15T10:00:00"
        phabfive countdown create "Sprint End" +2w
        phabfive countdown create "Release" +7d --description="v2.0 release"
        phabfive countdown create "Demo" +4h --subscribe=@me
    """
    countdown = _get_countdown_app()

    # Handle @me shortcut for subscribers
    subscriber_names = []
    if subscribe:
        for sub in subscribe:
            if sub == "@me":
                whoami = countdown.phab.user.whoami()
                subscriber_names.append(whoami.get("userName", sub))
            else:
                subscriber_names.append(sub)

    tag_list = list(tag) if tag else None

    if dry_run:
        # Parse epoch for display
        epoch_ts = countdown.parse_epoch(epoch)
        epoch_formatted = countdown._format_epoch(epoch_ts)

        print("[DRY RUN] Would create countdown:")
        print(f"  Title: {title}")
        print(f"  Epoch: {epoch_formatted}")
        if description:
            print(f"  Description: {description}")
        if tag_list:
            print(f"  Tags: {', '.join(tag_list)}")
        if subscriber_names:
            print(f"  Subscribers: {', '.join(subscriber_names)}")
        raise typer.Exit(0)

    try:
        result = countdown.create_countdown(
            title=title,
            epoch=epoch,
            description=description,
            tags=tag_list,
            subscribers=subscriber_names if subscriber_names else None,
        )
    except PhabfiveDataException as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    print(countdown.get_countdown_url(result["id"]))


@countdown_app.command()
def edit(
    ctx: typer.Context,
    countdown_id: str = typer.Argument(..., help="Countdown monogram (e.g., C123)"),
    title: Optional[str] = typer.Argument(None, help="New title"),
    title_opt: Optional[str] = typer.Option(
        None,
        "--title",
        hidden=True,
        help="New title (hidden, use positional argument instead)",
    ),
    epoch: Optional[str] = typer.Option(
        None,
        "--epoch",
        help="New target date (ISO 8601, +7d relative, or Unix timestamp)",
    ),
    description: Optional[str] = typer.Option(
        None, "--description", help="New description"
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
) -> None:
    """Edit an existing countdown.

    \b
    Examples:
        phabfive countdown edit C1 "New Title"
        phabfive countdown edit C1 --epoch=+14d
        phabfive countdown edit C1 --epoch="2026-07-01T00:00:00"
        phabfive countdown edit C1 --description="Updated description"
        phabfive countdown edit C1 --subscribe=@me --tag=project
    """
    _setup_output_options(ctx)
    countdown = _get_countdown_app()

    # Merge positional and option title (positional takes precedence)
    final_title = title or title_opt

    countdown_pattern = f"^{MONOGRAMS['countdown']}$"
    if not re.match(countdown_pattern, countdown_id):
        sys.stderr.write(
            f"Error: Invalid countdown ID '{countdown_id}'. Expected format: C123\n"
        )
        raise typer.Exit(1)

    numeric_id = int(countdown_id[1:])

    # Handle @me shortcut for subscribers
    subscriber_names = []
    if subscribe:
        for sub in subscribe:
            if sub == "@me":
                whoami = countdown.phab.user.whoami()
                subscriber_names.append(whoami.get("userName", sub))
            else:
                subscriber_names.append(sub)

    tag_list = list(tag) if tag else None

    try:
        result = countdown.edit_countdown(
            countdown_id=numeric_id,
            title=final_title,
            epoch=epoch,
            description=description,
            tags=tag_list,
            subscribers=subscriber_names if subscriber_names else None,
            dry_run=dry_run,
        )
    except PhabfiveDataException as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    if dry_run:
        print(f"[DRY RUN] Would edit {countdown_id}:")
        for change in result.get("changes", []):
            print(f"  {change['field']}: {change['new']}")
        if not result.get("changes"):
            print("  No changes specified")
    else:
        if result.get("changes"):
            print(f"Updated {countdown_id}")
            for change in result["changes"]:
                print(f"  {change['field']}: {change['new']}")
        else:
            print(result.get("message", "No changes made"))


@countdown_app.command()
def comment(
    ctx: typer.Context,
    countdown_id: str = typer.Argument(..., help="Countdown monogram (e.g., C123)"),
    text: Optional[str] = typer.Argument(
        None, help="Comment text (omit to open $EDITOR)"
    ),
) -> None:
    """Add a comment to a countdown.

    \b
    Examples:
        phabfive countdown comment C1 "Looking forward to this!"
        phabfive countdown comment C1  # opens $EDITOR
        echo "comment" | phabfive countdown comment C1 -
        phabfive C1 "Quick comment"  # monogram shortcut
    """
    from phabfive.editor import edit_text

    countdown = _get_countdown_app()

    countdown_pattern = f"^{MONOGRAMS['countdown']}$"
    if not re.match(countdown_pattern, countdown_id):
        sys.stderr.write(
            f"Error: Invalid countdown ID '{countdown_id}'. Expected format: C123\n"
        )
        raise typer.Exit(1)

    numeric_id = int(countdown_id[1:])

    final_text = None
    if text == "-":
        if sys.stdin.isatty():
            sys.stderr.write("Error: '-' requires input from stdin\n")
            raise typer.Exit(1)
        final_text = sys.stdin.read().strip()
    elif text is not None:
        final_text = text
    else:
        if not sys.stdin.isatty():
            sys.stderr.write("Error: Provide comment text or run interactively\n")
            raise typer.Exit(1)
        final_text = edit_text("", prefix="countdown-comment-", suffix=".remarkup")
        if final_text is None:
            print("Comment cancelled")
            raise typer.Exit(0)

    if not final_text:
        sys.stderr.write("Error: Comment cannot be empty\n")
        raise typer.Exit(1)

    try:
        countdown.add_countdown_comment(numeric_id, final_text)
        print(countdown.get_countdown_url(numeric_id))
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)
