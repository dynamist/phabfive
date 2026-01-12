# -*- coding: utf-8 -*-

"""Validation functions for Maniphest operations."""

import logging

from phabfive.exceptions import PhabfiveConfigException

log = logging.getLogger(__name__)


def validate_priority(priority):
    """
    Validate and normalize priority value.

    Parameters
    ----------
    priority : str
        Priority name (case-insensitive)

    Returns
    -------
    str
        Normalized priority value for API (lowercase)

    Raises
    ------
    PhabfiveConfigException
        If priority is invalid
    """
    # Map of user-friendly names to API values
    priority_map = {
        "unbreak": "unbreak",
        "unbreak now": "unbreak",
        "unbreak now!": "unbreak",
        "triage": "triage",
        "high": "high",
        "normal": "normal",
        "low": "low",
        "wish": "wish",
        "wishlist": "wish",
    }

    normalized = priority.lower().strip()

    if normalized not in priority_map:
        valid_choices = ["Unbreak", "Triage", "High", "Normal", "Low", "Wish"]
        raise PhabfiveConfigException(
            f"Invalid priority '{priority}'. Valid choices: {', '.join(valid_choices)}"
        )

    return priority_map[normalized]


def validate_status(status, api_status_map):
    """
    Validate and normalize status value.

    Parameters
    ----------
    status : str
        Status name (case-insensitive)
    api_status_map : dict
        Status map from API (result of _get_api_status_map())

    Returns
    -------
    str
        Normalized status key for API (lowercase)

    Raises
    ------
    PhabfiveConfigException
        If status is invalid
    """
    status_map = api_status_map.get("statusMap", {})

    # Build reverse map: display name (lowercase) -> key
    name_to_key = {v.lower(): k for k, v in status_map.items()}
    # Also allow using the key directly
    key_set = {k.lower() for k in status_map.keys()}

    normalized = status.lower().strip()

    # Check if it's a display name
    if normalized in name_to_key:
        return name_to_key[normalized]

    # Check if it's already a key
    if normalized in key_set:
        return normalized

    # Invalid status
    valid_choices = sorted(set(status_map.values()))
    raise PhabfiveConfigException(
        f"Invalid status '{status}'. Valid choices: {', '.join(valid_choices)}"
    )
