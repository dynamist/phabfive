# -*- coding: utf-8 -*-
"""Shell completion functions for phabfive CLI options."""

from typing import List

from phabfive.constants import PASTE_LANGUAGES

# Pattern prefixes for transition filters
PATTERN_PREFIXES = ["in:", "not:in:", "from:", "to:", "been:", "never:"]

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


def complete_priority(incomplete: str) -> List[str]:
    """Complete priority values - tries API first, falls back to defaults.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching priority completions
    """
    from phabfive.maniphest.fetchers import get_api_priority_names

    priorities = _get_values_with_api_fallback(
        lambda phab: get_api_priority_names(phab),
        DEFAULT_PRIORITY_VALUES,
    )
    return _complete_with_prefixes(incomplete, priorities)


def complete_status(incomplete: str) -> List[str]:
    """Complete status values - tries API first, falls back to defaults.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching status completions
    """
    from phabfive.maniphest.fetchers import get_api_status_map

    def fetch_statuses(phab):
        status_map = get_api_status_map(phab)
        # Return status keys (e.g., "open", "resolved")
        return list(status_map.get("statusMap", {}).keys())

    statuses = _get_values_with_api_fallback(fetch_statuses, DEFAULT_STATUS_VALUES)
    return _complete_with_prefixes(incomplete, statuses)


def complete_column(ctx, args: List[str], incomplete: str) -> List[str]:
    """Complete column names from the board specified by --tag.

    If --tag is provided, fetches actual column names from that board.
    Otherwise falls back to pattern prefixes and wildcard.

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
    # Get --tag value from parsed parameters
    tag_value = ctx.params.get("tag") if ctx else None

    # Directional navigation values
    directions = ["forward", "backward"]

    # Start with directions and pattern prefixes
    incomplete_lower = incomplete.lower()
    completions = [d for d in directions if d.startswith(incomplete_lower)]
    completions.extend(p for p in PATTERN_PREFIXES if p.startswith(incomplete))
    if not incomplete or "*".startswith(incomplete):
        completions.append("*")

    if tag_value:
        # Fetch columns from the specified board
        columns = _get_board_columns(tag_value)
        if columns:
            # Add actual column names
            completions.extend(
                c for c in columns if c.lower().startswith(incomplete_lower)
            )

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
