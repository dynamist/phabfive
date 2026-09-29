# -*- coding: utf-8 -*-

"""Validation functions for Maniphest operations."""

import logging

from phabfive.exceptions import PhabfiveConfigException, PhabfiveInputException
from phabfive.me import is_none

log = logging.getLogger(__name__)


def validate_assignment(assign, unassign):
    """
    Refuse an edit that both sets and removes the assignee.

    Parameters
    ----------
    assign : str or None
        The user to assign, or "@none" for nobody
    unassign : bool
        Whether to remove the assignee

    Raises
    ------
    PhabfiveInputException
        If both are given. "@none" with --unassign asks for the same thing
        twice, which is redundant rather than a conflict
    """
    if assign and unassign and not is_none(assign):
        raise PhabfiveInputException("--assign and --unassign cannot be used together")


def validate_task_relations(task_id, task_phid, relations):
    """
    Refuse parents and subtasks that would make a task relate to itself.

    The server refuses both too, but only as "Graph cycle detected" with the
    PHIDs involved. A cycle through other tasks is still left to it: finding
    one here would mean walking the graph.

    Parameters
    ----------
    task_id : str
        The numeric ID of the task being edited
    task_phid : str
        Its PHID
    relations : dict
        "parents" and "subtasks", each (added, removed) as
        ``resolve_task_phids`` returns them

    Raises
    ------
    PhabfiveInputException
        If the task is named as its own parent or subtask, or one task is
        added as both a parent and a subtask
    """
    for kind, (added, removed) in relations.items():
        for value, (phid, _) in (*added.items(), *removed.items()):
            if phid == task_phid:
                raise PhabfiveInputException(
                    f"T{task_id} cannot be its own {kind[:-1]}: '{value}'"
                )

    parents = {phid: name for phid, name in relations["parents"][0].values()}
    both = [name for phid, name in relations["subtasks"][0].values() if phid in parents]
    if both:
        raise PhabfiveInputException(
            f"Cannot add {', '.join(dict.fromkeys(both))} as both a parent "
            f"and a subtask of T{task_id}"
        )


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
