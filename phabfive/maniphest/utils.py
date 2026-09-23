# -*- coding: utf-8 -*-

"""Utility functions for Maniphest operations."""

import datetime
import logging
import time

log = logging.getLogger(__name__)


def days_ago_to_timestamp(days):
    """
    Convert days into a UNIX timestamp.

    The days are a **float**, because `parse_time_with_unit` answers "1h"
    with 1/24 and "12h" with 0.5. Truncating to whole days here made every
    sub-day value mean "now": `--created-after=1h` asked for tasks created
    after this instant and found none, and `--created-before=1h` asked for
    everything. Rounded to the nearest second instead, so an hour is an
    hour.
    """
    seconds = round(float(days) * 24 * 3600)
    return int(time.time()) - seconds


def time_constraint(value, key):
    """A TIME value as the epoch second a ``*.search`` constraint compares to.

    ``created-after``, ``created-before`` and their kin are written the same
    way for every application - "1h", "7d", "2w" - and reach three different
    endpoints as the same UNIX second, so the reading is here rather than
    copied into each app's constraint builder.

    Parameters
    ----------
    value : str, int, float or None
        The TIME value as written. None and "" mean the filter was not asked
        for, which is not the same as "0 days ago".
    key : str
        The spec key or flag to name in the error, e.g. ``"created-after"``.

    Returns
    -------
    int or None
        The epoch second, or None when there was no value.

    Raises
    ------
    PhabfiveInputException
        The value is not a TIME. The key is prefixed onto the message, so a
        spec holding two bad times says which one it is talking about.
    """
    from phabfive.exceptions import PhabfiveInputException
    from phabfive.spec.times import parse_time_with_unit

    if value in (None, ""):
        return None

    try:
        return days_ago_to_timestamp(parse_time_with_unit(value))
    except PhabfiveInputException as e:
        raise PhabfiveInputException(f"{key}: {e}")


def format_timestamp(timestamp):
    """
    Convert UNIX timestamp to ISO 8601 string (readable time format).
    """
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _task_id(task):
    """Task id as an int, tolerating fixtures and partial API responses."""
    try:
        return int(task.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _task_int_field(task, name):
    """A numeric field under `fields`, defaulting to 0 when absent."""
    value = task.get("fields", {}).get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _task_priority(task):
    """Numeric task priority.

    Falls back to 0 when the response carries only `priority.name`, which is
    how several test fixtures are shaped. The names the API returns
    ("Unbreak Now!", "Needs Triage", ...) do not map cleanly onto
    PRIORITY_VALUES, so there is no better guess to make.
    """
    value = task.get("fields", {}).get("priority", {}).get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _task_closed(task):
    """Epoch the task was closed at, or None while it is still open."""
    return task.get("fields", {}).get("dateClosed")


def _task_title(task):
    """Case-folded task title.

    An approximation of the column's utf8mb4_unicode_ci collation, not a
    match, so a client-side title sort can differ from the server's for
    accented or non-Latin titles.
    """
    return (task.get("fields", {}).get("name") or "").casefold()


# (field, direction) -> (sort key, reverse). Every key ends in a task-id term,
# and ids are unique, so each entry is a total order: equal tasks can never
# come back in a different order on the next run.
#
# The entries marked below reproduce ManiphestTaskQuery::getBuiltinOrders()
# exactly, which matters because the multi-project path has to merge separate
# result sets locally and must not disagree with the server about ordering.
MANIPHEST_SORT_KEYS = {
    # Phorge vector ('priority', 'id')
    ("priority", "desc"): (lambda t: (_task_priority(t), _task_id(t)), True),
    ("priority", "asc"): (lambda t: (_task_priority(t), _task_id(t)), False),
    # Phorge vector ('updated', 'id')
    ("updated", "desc"): (
        lambda t: (_task_int_field(t, "dateModified"), _task_id(t)),
        True,
    ),
    # Phorge vector ('-updated', '-id'), its "outdated" order
    ("updated", "asc"): (
        lambda t: (_task_int_field(t, "dateModified"), _task_id(t)),
        False,
    ),
    # Phorge vectors ('id') and ('-id')
    ("created", "desc"): (lambda t: (_task_id(t),), True),
    ("created", "asc"): (lambda t: (_task_id(t),), False),
    # Phorge vector ('closed', 'id') with null closedEpoch sorted to the tail.
    # The sentinel flips with the direction so open tasks stay last either way.
    ("closed", "desc"): (
        lambda t: (
            1 if _task_closed(t) else 0,
            _task_closed(t) or 0,
            _task_id(t),
        ),
        True,
    ),
    ("closed", "asc"): (
        lambda t: (
            0 if _task_closed(t) else 1,
            _task_closed(t) or 0,
            _task_id(t),
        ),
        False,
    ),
    # Phorge vector ('title', 'id'); the title column is declared reverse, so
    # titles read A-Z while ids still tie-break newest first.
    ("title", "asc"): (lambda t: (_task_title(t), -_task_id(t)), False),
    ("title", "desc"): (lambda t: (_task_title(t), -_task_id(t)), True),
}

# (field, direction) -> the builtin order name sent to maniphest.search.
#
# Phorge has no builtin for priority ascending, closed ascending or title
# descending, so those send the same field's descending builtin and let the
# client-side sort flip it. That is sound because every page is fetched before
# anything is sorted or truncated.
PHORGE_ORDER_KEYS = {
    ("priority", "desc"): "priority",
    ("priority", "asc"): "priority",
    ("updated", "desc"): "updated",
    ("updated", "asc"): "outdated",
    ("created", "desc"): "newest",
    ("created", "asc"): "oldest",
    ("closed", "desc"): "closed",
    ("closed", "asc"): "closed",
    ("title", "asc"): "title",
    ("title", "desc"): "title",
    ("relevance", None): "relevance",
}


def sort_tasks(tasks, field, direction):
    """
    Order a list of task dicts by one of the `--order` choices.

    Returns the list untouched for orders with no client-side key, which today
    means only `relevance`: the response carries no rank field, so the server's
    ordering is the only one there is.
    """
    entry = MANIPHEST_SORT_KEYS.get((field, direction))
    if entry is None:
        return tasks

    key, reverse = entry
    return sorted(tasks, key=key, reverse=reverse)
