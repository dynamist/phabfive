# -*- coding: utf-8 -*-
"""Shell completion functions for phabfive CLI options."""

from typing import List

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


def complete_tag(incomplete: str) -> List[str]:
    """Complete tag (project) names from API.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching project name completions
    """

    def fetch_project_names(phab):
        # Fetch projects - project.search returns up to 100 by default
        result = phab.project.search(constraints={})
        return [proj["fields"]["name"] for proj in result.get("data", [])]

    # No default values for tags - they are instance-specific
    tags = _get_values_with_api_fallback(fetch_project_names, [])

    # Case-insensitive prefix matching
    incomplete_lower = incomplete.lower()
    return [t for t in tags if t.lower().startswith(incomplete_lower)]
