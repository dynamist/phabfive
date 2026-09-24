# -*- coding: utf-8 -*-
"""Paste commands for phabfive CLI."""

import re
import sys
from typing import List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.completers import (
    complete_language,
    complete_tag_list,
    complete_user_list,
    complete_user_filter,
)
from phabfive.cli.output import (
    _echo_no_match_hint,
    _exit_with_help,
    _get_output_format,
    _setup_output_options,
    is_machine_format,
)
from phabfive.constants import (
    MONOGRAMS,
    PASTE_ORDER_DEFAULT,
    PASTE_ORDER_DIRECTIONS,
    PASTE_ORDER_FIELDS,
    PASTE_STATUS_CHOICES,
)
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
)
from phabfive.users import resolve_user_phid, resolve_user_phids
from phabfive.cli.completers import complete_policy
from phabfive.cli.editor import resolve_assume_yes
from phabfive.policy import POLICY_GRAMMAR, validate_policy_value
from phabfive.options import any_list_value, split_list_option
from phabfive.ordering import complete_order_value
from phabfive.paste.core import build_paste_search_constraints
from phabfive.paste.display import display_pastes

paste_app = typer.Typer(
    cls=AgentFooterGroup, help="The paste app", no_args_is_help=True
)


def complete_paste_order(incomplete: str) -> List[str]:
    """Complete `paste search --order`: fields first, directions after ":"."""
    return complete_order_value(incomplete, PASTE_ORDER_FIELDS, PASTE_ORDER_DIRECTIONS)


def complete_paste_status(incomplete: str) -> List[str]:
    """Complete `paste search --status`: the two statuses paste.search knows."""
    return [status for status in PASTE_STATUS_CHOICES if status.startswith(incomplete)]


def _get_paste_app():
    """Get Paste app instance with config error handling."""
    from phabfive.cli.apps import get_app
    from phabfive.paste import Paste

    return get_app(Paste)


def _show_pastes_after_write(ctx, paste_instance, paste_ids):
    """Emit the records `show` gives for the pastes a write command touched.

    A create, an edit or a comment answers a machine-readable format with
    exactly what ``paste show`` answers with for the object it just wrote,
    so no second, parallel "result" shape has to be invented or kept in
    step. The maniphest commands do the same (#344).

    Parameters
    ----------
    ctx : typer.Context
        The command context, carrying the format the caller asked for
    paste_instance : Paste
        The instance the write went through
    paste_ids : list
        Paste IDs, numeric and without the P prefix
    """
    result = paste_instance.paste_show([int(paste_id) for paste_id in paste_ids])
    display_pastes(result, _get_output_format(ctx), paste_instance)


@paste_app.command()
def search(
    ctx: typer.Context,
    text_query: Optional[str] = typer.Argument(
        None, help="Free-text search in paste title"
    ),
    with_template: Optional[str] = typer.Option(
        None,
        "--with",
        help="Load the search from a YAML search spec; every option below "
        "overrides what the spec says",
    ),
    author: Optional[str] = typer.Option(
        None,
        "--author",
        help="Filter by author (username, @me or user PHID)",
        autocompletion=complete_user_filter,
    ),
    ids: Optional[List[str]] = typer.Option(
        None,
        "--ids",
        help="Only these pastes (P123 or 123), with every other filter still "
        "applied (repeatable, or comma-separated)",
    ),
    phids: Optional[List[str]] = typer.Option(
        None,
        "--phids",
        help="Only these paste PHIDs, with every other filter still applied "
        "(repeatable, or comma-separated)",
    ),
    language: Optional[List[str]] = typer.Option(
        None,
        "--language",
        help="Pastes in any of these languages (repeatable, or comma-separated)",
        autocompletion=complete_language,
    ),
    status: Optional[List[str]] = typer.Option(
        None,
        "--status",
        help="Pastes of any of these statuses: active, archived "
        "(repeatable, or comma-separated; default: both)",
        autocompletion=complete_paste_status,
    ),
    created_after: Optional[str] = typer.Option(
        None,
        "--created-after",
        help="Pastes created within TIME, e.g. 1h, 7d, 2w",
    ),
    created_before: Optional[str] = typer.Option(
        None,
        "--created-before",
        help="Pastes created more than TIME ago, e.g. 1h, 7d, 2w",
    ),
    limit: int = typer.Option(
        100, "--limit", "-l", help="Maximum results to return, 0 for all"
    ),
    order: Optional[str] = typer.Option(
        None,
        "--order",
        "-o",
        help="Sort results by " + "|".join(PASTE_ORDER_FIELDS) + ", optionally "
        f"suffixed with :asc or :desc  [default: {PASTE_ORDER_DEFAULT}]",
        autocompletion=complete_paste_order,
    ),
) -> None:
    """Search and list pastes with optional filters.

    A paste has no modified-date filter: PhabricatorPasteQuery has no such
    constraint, so --created-after and --created-before are the only dates
    a search can narrow on.

    \b
    Examples:
        phabfive paste search "script"
        phabfive paste search --author=@me
        phabfive paste search "config" --author=@me
        phabfive paste search "config" --limit=250
        phabfive paste search --language=python --created-after=7d
        phabfive paste search --status=archived --order=created:asc
        phabfive paste search --ids=P12,P13
        phabfive paste search --author=@me --limit=0
        phabfive paste search --with searches.yaml
        phabfive --format=yaml paste search "notes"
    """
    # Require at least one search criterion - unless a spec carries them,
    # which is checked per search once the spec has been read. --order is
    # not one: it says how to sort a search, not which pastes to look at.
    # The list options are asked through `any_list_value`, not tested raw:
    # `--ids=,` is truthy as typer collected it and empty once it is parsed,
    # so testing it raw would lift the guard and then send no constraint.
    criteria = [
        text_query,
        author,
        created_after,
        created_before,
        any_list_value(ids, phids, language, status),
    ]
    if not with_template and not any(criteria):
        _exit_with_help(ctx)

    _setup_output_options(ctx)

    # Refused rather than ignored: none of these is a search spec key yet
    from phabfive.cli.search_spec import refuse_unspecced

    refuse_unspecced(
        with_template,
        {
            "--ids": ids,
            "--phids": phids,
            "--language": language,
            "--status": status,
            "--created-after": created_after,
            "--created-before": created_before,
            "--order": order,
        },
    )

    paste = _get_paste_app()

    if with_template:
        from phabfive.cli.search_spec import load_search_spec, run_search_spec

        run_search_spec(
            ctx,
            paste,
            load_search_spec(with_template),
            # Keyed as a spec spells the key; None means "not given", so a
            # flag nobody typed cannot clobber the spec's value
            overrides={
                "text_query": text_query,
                "author": author,
                "limit": limit if limit != 100 else None,
            },
        )
        return

    # A username, @username, @me or user PHID
    author_phids = None
    if author:
        author_phid, _ = resolve_user_phid(paste.phab, author, option="--author")
        author_phids = [author_phid]

    # The constraints are built and checked in the library, so a spec and a
    # flag are answered by the same check with the same message
    try:
        constraints = build_paste_search_constraints(
            text_query=text_query,
            author_phids=author_phids,
            ids=split_list_option(ids),
            phids=split_list_option(phids),
            languages=split_list_option(language),
            statuses=split_list_option(status),
            created_after=created_after,
            created_before=created_before,
        )

        # A limit is how many pastes to return, not the page size to ask
        # for, and 0 - like maniphest search - means every match
        result = paste.paste_search(
            constraints=constraints if constraints else None,
            limit=limit if limit > 0 else None,
            order=order,
        )
    # PhabfiveInputException is a PhabfiveConfigException and needs no row
    # of its own; see phabfive/exceptions.py.
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if not result["pastes"]:
        typer.echo("No pastes found", err=True)
        _echo_no_match_hint(text_query)
        return

    # The record `paste show` answers with, less the content, so a filter
    # written against one command works on the other
    display_pastes(result, _get_output_format(ctx), paste, tabular=True)


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
        None,
        "--tag",
        help="Add to project (repeatable, comma-separated)",
        autocompletion=complete_tag_list,
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None,
        "--subscribe",
        help="Add a subscriber (username, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list,
    ),
    add_subscriber: Optional[List[str]] = typer.Option(
        None,
        "--add-subscriber",
        hidden=True,
        help="Alias for --subscribe",
        autocompletion=complete_user_list,
    ),
    visible_to: Optional[str] = typer.Option(
        None,
        "--visible-to",
        help=f"Set who can see it ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    ),
    editable_by: Optional[str] = typer.Option(
        None,
        "--editable-by",
        help=f"Set who can edit it ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without creating"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Create without confirming"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review the new paste and confirm"
    ),
) -> None:
    """Create a new paste.

    \b
    Examples:
        phabfive paste create "My Script" script.py
        phabfive paste create "Notes" --content="Some text"
        echo "content" | phabfive paste create "From stdin" --content=-
        phabfive paste create "Code" --language=python  # opens $EDITOR
        phabfive paste create "Notes" --subscribe=@me --tag=project
        phabfive paste create "Secret" --content=... --visible-to='#platform'
    """
    # A policy outside the grammar is refused before the instance is
    # reached: Conduit reads an unknown value as a policy nobody satisfies,
    # and so answers a typo with a self-lockout error.
    try:
        for option, value in (
            ("--visible-to", visible_to),
            ("--editable-by", editable_by),
        ):
            validate_policy_value(value, option=option)
    except PhabfiveConfigException as e:
        sys.stderr.write(f"ERROR: {e}\n")
        raise typer.Exit(1)

    try:
        assume_yes = resolve_assume_yes(yes, False, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    _setup_output_options(ctx)
    paste = _get_paste_app()

    # A machine-readable format answers with the record `show` would give
    # for the paste that was created. A dry run wrote nothing, so it has no
    # record to give: its preview goes to stderr and stdout stays empty.
    output_format = _get_output_format(ctx)
    machine = is_machine_format(output_format)
    preview = sys.stderr if machine else sys.stdout

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
        from phabfive.cli.editor import edit_text

        suffix = f".{language}" if language else ".txt"
        final_content = edit_text("", prefix="paste-", suffix=suffix)
        if final_content is None:
            print("Paste creation cancelled", file=preview)
            raise typer.Exit(0)

    else:
        sys.stderr.write(
            "Error: No content provided. Use a file, --content, or run interactively.\n"
        )
        raise typer.Exit(1)

    # Usernames, @usernames, @me or user PHIDs, sent as the usernames they
    # name - which is also what the preview shows
    subscribe = [*(subscribe or []), *(add_subscriber or [])]
    subscriber_names = []
    if subscribe:
        users = resolve_user_phids(
            paste.phab, split_list_option(subscribe), option="--subscribe"
        )
        subscriber_names = list(
            dict.fromkeys(username or phid for phid, username in users.values())
        )

    # Handle tags
    tag_list = split_list_option(tag) or None

    def show_preview(header):
        print(header, file=preview)
        print(f"  Name: {final_title}", file=preview)
        if language:
            print(f"  Language: {language}", file=preview)
        if tag_list:
            print(f"  Tags: {', '.join(tag_list)}", file=preview)
        if subscriber_names:
            print(f"  Subscribers: {', '.join(subscriber_names)}", file=preview)
        if visible_to:
            print(f"  Visible To: {visible_to}", file=preview)
        if editable_by:
            print(f"  Editable By: {editable_by}", file=preview)
        # Show content preview
        lines = final_content.split("\n")
        if len(lines) <= 5:
            print("  Content:", file=preview)
            for line in lines:
                print(f"    {line}", file=preview)
        else:
            print(
                f"  Content: ({len(lines)} lines, {len(final_content)} chars)",
                file=preview,
            )

    if dry_run:
        show_preview("[DRY RUN] Would create paste:")
        raise typer.Exit(0)

    if interactive:
        from phabfive.cli.editor import confirm_apply

        show_preview("Would create paste:")
        confirmed, return_code = confirm_apply(assume_yes)
        if not confirmed:
            sys.stderr.write("Nothing was created.\n")
            raise typer.Exit(return_code or 0)

    # Create the paste
    try:
        result = paste.create_paste_from_content(
            title=final_title,
            content=final_content,
            language=language,
            tags=tag_list,
            subscribers=subscriber_names,
            visible_to=visible_to,
            editable_by=editable_by,
        )
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        sys.stderr.write(f"ERROR: {e}\n")
        raise typer.Exit(1)

    if machine:
        _show_pastes_after_write(ctx, paste, [result["id"]])
        return

    # Output the result (full URL, consistent with maniphest create)
    print(paste.get_paste_url(result["id"]))


@paste_app.command()
def show(
    ctx: typer.Context,
    paste_ids: List[str] = typer.Argument(
        ..., help="Paste monogram(s) (e.g., P1 P2 or P1,P2)"
    ),
    show_content: bool = typer.Option(
        True, "--show-content/--no-content", help="Show paste content"
    ),
) -> None:
    """Show details for one or more pastes.

    \b
    Examples:
        phabfive paste show P1
        phabfive paste show P1 P2 --no-content
        phabfive paste show P1,P2
        phabfive --format=yaml paste show P1
    """
    _setup_output_options(ctx)
    paste = _get_paste_app()

    # Support both space-separated (P1 P2) and comma-separated (P1,P2)
    all_ids: list[str] = []
    for id_arg in paste_ids:
        all_ids.extend(part.strip() for part in id_arg.split(",") if part.strip())

    # Validate all paste ID formats
    paste_pattern = f"^{MONOGRAMS['paste']}$"
    ids = []
    for paste_id in all_ids:
        if not re.match(paste_pattern, paste_id):
            typer.echo(
                f"Invalid paste ID '{paste_id}'. Expected format: P123", err=True
            )
            raise typer.Exit(1)
        ids.append(int(paste_id[1:]))

    result = paste.paste_show(ids, show_content=show_content)

    display_pastes(result, _get_output_format(ctx), paste)

    # A paste that does not exist is a failed lookup, not an empty result
    if result is None or result.get("missing_ids"):
        raise typer.Exit(1)


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
        None,
        "--tag",
        help="Add to project (repeatable, comma-separated)",
        autocompletion=complete_tag_list,
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None,
        "--subscribe",
        help="Add a subscriber (username, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list,
    ),
    add_subscriber: Optional[List[str]] = typer.Option(
        None,
        "--add-subscriber",
        hidden=True,
        help="Alias for --subscribe",
        autocompletion=complete_user_list,
    ),
    unsubscribe: Optional[List[str]] = typer.Option(
        None,
        "--unsubscribe",
        help="Remove a subscriber (username, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list,
    ),
    remove_subscriber: Optional[List[str]] = typer.Option(
        None,
        "--remove-subscriber",
        hidden=True,
        help="Alias for --unsubscribe",
        autocompletion=complete_user_list,
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview changes without applying"
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Apply without confirming",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review each change and confirm"
    ),
    force: bool = typer.Option(
        False, "--force", hidden=True, help="Deprecated alias for --yes"
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
        phabfive paste edit P1 --unsubscribe=@me
        phabfive paste edit P1 "Test" --dry-run
    """
    from phabfive.cli.editor import confirm_text_change, edit_text

    try:
        force = resolve_assume_yes(yes, force, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    _setup_output_options(ctx)
    paste = _get_paste_app()

    output_format = _get_output_format(ctx)
    machine = is_machine_format(output_format)
    preview = sys.stderr if machine else sys.stdout

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
            print("Edit cancelled (no changes)", file=preview)
            raise typer.Exit(0)
        final_content = new_content
    elif content is not None:
        final_content = content

    # Handle tags
    tag_list = split_list_option(tag) or None

    # A single object applies directly; --interactive asks first.
    if final_content is not None and not dry_run and interactive:
        confirmed, return_code = confirm_text_change(
            current_paste["content"], final_content, False, filename="content"
        )
        if not confirmed:
            raise typer.Exit(return_code or 0)

    if final_title is not None and not dry_run and interactive:
        current_title = current_paste.get("title", "")
        if final_title != current_title:
            confirmed, return_code = confirm_text_change(
                current_title, final_title, False, filename="title"
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
        subscribers=split_list_option([*(subscribe or []), *(add_subscriber or [])]),
        unsubscribers=split_list_option(
            [*(unsubscribe or []), *(remove_subscriber or [])]
        ),
        current_subscribers=current_paste.get("subscriberPHIDs"),
        dry_run=dry_run,
    )

    # Output result
    if dry_run:
        from phabfive.cli.editor import show_diff

        print(f"[DRY RUN] Would edit {paste_id}:", file=preview)
        for change in result.get("changes", []):
            if change["field"] == "Title":
                # Show unified diff for title
                print(file=preview)
                show_diff(
                    current_paste.get("title", ""),
                    final_title,
                    filename="title",
                    file=preview,
                )
            else:
                print(f"  {change['field']}: {change['new']}", file=preview)
        if not result.get("changes"):
            print(f"  {result.get('message', 'No changes specified')}", file=preview)
        return

    if result.get("changes"):
        print(f"Updated {paste_id}", file=preview)
        for change in result["changes"]:
            print(f"  {change['field']}: {change['new']}", file=preview)
    else:
        print(result.get("message", "No changes made"), file=preview)

    # An edit that needed no transaction still has a record to answer with:
    # "nothing to change" is an answer about the paste, not an absence of
    # one, and a caller parsing the stream should not have to read an empty
    # stdout as it. The maniphest commands settled this the same way (#344).
    if machine:
        _show_pastes_after_write(ctx, paste, [numeric_id])


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
    from phabfive.cli.editor import edit_text

    _setup_output_options(ctx)
    paste = _get_paste_app()

    output_format = _get_output_format(ctx)
    machine = is_machine_format(output_format)
    preview = sys.stderr if machine else sys.stdout

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
            print("Comment cancelled", file=preview)
            raise typer.Exit(0)

    if not final_text:
        sys.stderr.write("Error: Comment cannot be empty\n")
        raise typer.Exit(1)

    # Add the comment
    try:
        paste.add_paste_comment(numeric_id, final_text)

        if machine:
            _show_pastes_after_write(ctx, paste, [numeric_id])
        else:
            print(paste.get_paste_url(numeric_id))
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)
