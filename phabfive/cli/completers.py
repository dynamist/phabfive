# -*- coding: utf-8 -*-
"""Shell completion functions for phabfive CLI options."""

from typing import List, Optional

from phabfive.constants import PASTE_LANGUAGES, REPO_STATUS_CHOICES

# Pattern prefixes for transition filters
PATTERN_PREFIXES = ["in:", "not:in:", "from:", "to:", "been:", "never:"]

# Keywords accepted by the priority and status filters
FILTER_DIRECTION_KEYWORDS = ["raised", "lowered"]

# Keywords accepted when editing a priority
PRIORITY_CHANGE_KEYWORDS = ["raise", "lower"]

# Keywords accepted when editing a column, and by the column filter
COLUMN_DIRECTIONS = ["forward", "backward"]

# Default values used when API is unavailable
DEFAULT_PRIORITY_VALUES = [
    "unbreak",
    "triage",
    "high",
    "normal",
    "low",
    "wish",
]
DEFAULT_STATUS_VALUES = [
    "open",
    "resolved",
    "wontfix",
    "invalid",
    "duplicate",
]


def _get_values_with_api_fallback(fetch_func, default_values: List[str]) -> List[str]:
    """Try to fetch values from API, fall back to defaults.

    Parameters
    ----------
    fetch_func : callable
        Function that takes a phab client and returns a list of values
    default_values : list
        Default values to use if API call fails

    Returns
    -------
    list
        Values from API or defaults
    """
    import contextlib
    import io

    try:
        from phabfive.core import Phabfive

        # Suppress warnings during completion (they break shell completion output)
        with contextlib.redirect_stderr(io.StringIO()):
            pf = Phabfive()
            return fetch_func(pf.phab)
    except Exception:
        return default_values


def _complete_with_prefixes(incomplete: str, values: List[str]) -> List[str]:
    """Complete values with pattern prefix support.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed
    values : list
        Available values to complete

    Returns
    -------
    list
        Matching completions
    """
    # If starts with a pattern prefix, complete the value after prefix
    for prefix in PATTERN_PREFIXES:
        if incomplete.startswith(prefix):
            remainder = incomplete[len(prefix) :]
            return [f"{prefix}{v}" for v in values if v.startswith(remainder)]

    # Complete bare values
    completions = []
    completions.extend(v for v in values if v.startswith(incomplete))

    # For prefixes, offer full prefix:value combinations instead of bare prefix
    # This allows continued completion after selecting (e.g., "in:" -> "in:high")
    for prefix in PATTERN_PREFIXES:
        if prefix.startswith(incomplete):
            completions.extend(f"{prefix}{v}" for v in values)

    return completions


def _get_priorities() -> List[str]:
    """Get priority names - tries API first, falls back to defaults."""
    from phabfive.maniphest.fetchers import get_api_priority_names

    return _get_values_with_api_fallback(
        lambda phab: get_api_priority_names(phab),
        DEFAULT_PRIORITY_VALUES,
    )


def _get_statuses() -> List[str]:
    """Get status keys (e.g., "open", "resolved") - tries API first, falls back to defaults."""
    from phabfive.maniphest.fetchers import get_api_status_map

    def fetch_statuses(phab):
        status_map = get_api_status_map(phab)
        return list(status_map.get("statusMap", {}).keys())

    return _get_values_with_api_fallback(fetch_statuses, DEFAULT_STATUS_VALUES)


def _starting_with(incomplete: str, values: List[str]) -> List[str]:
    """Return the values that start with the incomplete text."""
    return [v for v in values if v.startswith(incomplete)]


def complete_priority(incomplete: str) -> List[str]:
    """Complete priority values for setting a priority (maniphest create).

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching priority completions
    """
    return _starting_with(incomplete, _get_priorities())


def complete_priority_change(incomplete: str) -> List[str]:
    """Complete priority values for changing a priority (edit).

    Like complete_priority, plus the raise/lower navigation keywords.
    """
    return _starting_with(incomplete, _get_priorities() + PRIORITY_CHANGE_KEYWORDS)


def complete_priority_filter(incomplete: str) -> List[str]:
    """Complete priority filter patterns (maniphest search).

    Offers priority names, pattern prefixes (e.g., "in:high") and the
    raised/lowered keywords.
    """
    completions = _complete_with_prefixes(incomplete, _get_priorities())
    completions.extend(_starting_with(incomplete, FILTER_DIRECTION_KEYWORDS))
    return completions


def complete_status(incomplete: str) -> List[str]:
    """Complete status values for setting a status (create, edit).

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching status completions
    """
    return _starting_with(incomplete, _get_statuses())


def complete_status_filter(incomplete: str) -> List[str]:
    """Complete status filter patterns (maniphest search).

    Offers status names, pattern prefixes (e.g., "in:open") and the
    raised/lowered keywords.
    """
    completions = _complete_with_prefixes(incomplete, _get_statuses())
    completions.extend(_starting_with(incomplete, FILTER_DIRECTION_KEYWORDS))
    return completions


def _board_context(ctx) -> Optional[str]:
    """Get the board name given with --tag, if any.

    --tag is a single value on search and edit but repeatable on create,
    where the first tag is the board context.
    """
    tag_value = ctx.params.get("tag") if ctx else None
    if isinstance(tag_value, (list, tuple)):
        tag_value = tag_value[0] if tag_value else None
    return tag_value or None


def _matching_board_columns(ctx, incomplete: str) -> List[str]:
    """Return column names on the --tag board that match the incomplete text."""
    tag_value = _board_context(ctx)
    if not tag_value:
        return []

    incomplete_lower = incomplete.lower()
    return [
        c
        for c in _get_board_columns(tag_value)
        if c.lower().startswith(incomplete_lower)
    ]


def complete_column(ctx, args: List[str], incomplete: str) -> List[str]:
    """Complete column names from the board specified by --tag (maniphest create).

    Parameters
    ----------
    ctx : click.Context
        Click context with parsed parameters
    args : list
        Command line arguments (unused, ctx.params preferred)
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching column completions
    """
    return _matching_board_columns(ctx, incomplete)


def complete_column_change(ctx, args: List[str], incomplete: str) -> List[str]:
    """Complete column names for moving a task (edit).

    Like complete_column, plus the forward/backward navigation keywords.
    """
    incomplete_lower = incomplete.lower()
    completions = [d for d in COLUMN_DIRECTIONS if d.startswith(incomplete_lower)]
    completions.extend(_matching_board_columns(ctx, incomplete))
    return completions


def complete_column_filter(ctx, args: List[str], incomplete: str) -> List[str]:
    """Complete column filter patterns (maniphest search).

    Offers the forward/backward keywords, pattern prefixes, the wildcard
    and, if --tag is given, column names from that board.
    """
    incomplete_lower = incomplete.lower()
    completions = [d for d in COLUMN_DIRECTIONS if d.startswith(incomplete_lower)]
    completions.extend(p for p in PATTERN_PREFIXES if p.startswith(incomplete))
    if not incomplete or "*".startswith(incomplete):
        completions.append("*")
    completions.extend(_matching_board_columns(ctx, incomplete))
    return completions


def _get_board_columns(tag_name: str) -> List[str]:
    """Fetch column names for a board/project.

    Parameters
    ----------
    tag_name : str
        Project/board name

    Returns
    -------
    list
        Column names, or empty list if not found
    """
    import contextlib
    import io

    try:
        from phabfive.core import Phabfive
        from phabfive.maniphest.resolvers import resolve_project_phids

        with contextlib.redirect_stderr(io.StringIO()):
            pf = Phabfive()
            # Resolve project name to PHID
            phids = resolve_project_phids(pf.phab, tag_name)
            if not phids:
                return []

            board_phid = phids[0]  # Use first match

            # Fetch columns for this board
            result = pf.phab.project.column.search(
                constraints={"projects": [board_phid]}
            )

            if not result.get("data"):
                return []

            # Extract column names
            return [col["fields"]["name"] for col in result["data"]]
    except Exception:
        return []


# Stop fetching projects for --tag completion after this many matches
TAG_COMPLETION_LIMIT = 500


def _fetch_projects_named(phab, incomplete: str) -> list:
    """Fetch projects whose name has a word starting with the incomplete text.

    Uses the project.search "name" constraint so matching happens on the
    server, and follows the result cursor up to TAG_COMPLETION_LIMIT.
    """
    constraints = {"name": incomplete} if incomplete.strip() else {}
    projects = []
    after = None

    while len(projects) < TAG_COMPLETION_LIMIT:
        kwargs = {"constraints": constraints, "limit": 100}
        if after:
            kwargs["after"] = after
        result = phab.project.search(**kwargs)
        projects.extend(result.get("data", []))
        after = (result.get("cursor") or {}).get("after")
        if not after:
            break

    return projects


def complete_tag(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete tag (project) names from API.

    Matching is case-insensitive. Completions follow the case the user typed
    (e.g. "gun" -> "gunnar-core"), because Typer drops completions that don't
    start with the typed text; project names resolve case-insensitively, so
    the value still works.

    A name shared by several projects (e.g. milestones named "Sprint 1" in
    different parents) is offered once, with a description listing the
    project IDs to use instead. Milestones and subprojects are described
    with their parent. Descriptions are shown by zsh and fish.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching project names, as (name, description) tuples where a
        description applies
    """
    # No default values for tags - they are instance-specific
    projects = _get_values_with_api_fallback(
        lambda phab: _fetch_projects_named(phab, incomplete), []
    )

    incomplete_lower = incomplete.lower()
    by_name = {}
    for proj in projects:
        name = proj["fields"]["name"]
        if name.lower().startswith(incomplete_lower):
            by_name.setdefault(name.lower(), []).append(proj)

    completions = []
    for _, matches in sorted(by_name.items()):
        value = _in_typed_case(incomplete, matches[0]["fields"]["name"])

        if len(matches) > 1:
            ids = ", ".join(
                _describe_project_id(proj)
                for proj in sorted(matches, key=lambda proj: proj["id"])
            )
            completions.append((value, f"ambiguous, use the ID: {ids}"))
        elif matches[0]["fields"].get("parent"):
            completions.append((value, f"in {matches[0]['fields']['parent']['name']}"))
        else:
            completions.append(value)

    return completions


def _in_typed_case(incomplete: str, name: str) -> str:
    """Return name so that it starts with the incomplete text as typed."""
    if name.startswith(incomplete):
        return name
    if incomplete.islower():
        return name.lower()
    if incomplete.isupper():
        return name.upper()
    return incomplete + name[len(incomplete) :]


def _describe_project_id(proj) -> str:
    """Describe a project as its ID plus parent name, e.g. "9 (QA)"."""
    parent = proj["fields"].get("parent")
    return f"{proj['id']} ({parent['name']})" if parent else str(proj["id"])


def complete_language(incomplete: str) -> List[str]:
    """Complete programming language values for syntax highlighting.

    Uses the default languages from Phabricator/Phorge pygments.dropdown-choices.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching language completions
    """
    incomplete_lower = incomplete.lower()
    return [lang for lang in PASTE_LANGUAGES if lang.startswith(incomplete_lower)]


# Credential types accepted by passphrase search --type ("ssh" is an alias for "key")
PASSPHRASE_TYPES = ["password", "token", "key", "ssh", "note"]


def _complete_fixed(incomplete: str, values: List[str]) -> List[str]:
    """Complete from a fixed list of values."""
    return [v for v in values if v.startswith(incomplete)]


def complete_repo_status(incomplete: str) -> List[str]:
    """Complete the repository status filter for diffusion repo list.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching status completions
    """
    return _complete_fixed(incomplete, REPO_STATUS_CHOICES + ["all"])


def complete_passphrase_type(incomplete: str) -> List[str]:
    """Complete credential types for passphrase search --type.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching credential type completions
    """
    return _complete_fixed(incomplete, PASSPHRASE_TYPES)
