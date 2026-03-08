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
    try:
        from phabfive.core import Phabfive

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


def complete_column(incomplete: str) -> List[str]:
    """Complete column patterns (prefixes + wildcard).

    Column names are board-specific, so we only complete pattern prefixes
    and the wildcard character.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching column completions
    """
    completions = [p for p in PATTERN_PREFIXES if p.startswith(incomplete)]
    if not incomplete or "*".startswith(incomplete):
        completions.append("*")
    return completions
