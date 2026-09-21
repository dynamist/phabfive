# -*- coding: utf-8 -*-

"""
Status transition pattern matching for task statuses.

This module provides pattern matching for task status changes,
supporting conditions like 'from:Open', 'to:Resolved', 'in:Blocked', 'raised', 'lowered'.

It also owns the scope keywords 'open', 'closed' and 'any', which say which
statuses a search reaches at all rather than how a task got there. A search
reaches open tasks unless told otherwise, so 'in:Resolved' on its own can never
match - 'any+in:Resolved' can. See resolve_status_scope.
"""

import logging

from phabfive.transitions.base import (
    parse_condition_parts,
    parse_direction,
    parse_negation_prefix,
    split_pattern_groups,
)

log = logging.getLogger(__name__)

# Valid condition types for status patterns
# Types that require a status value (e.g., "in:Open")
VALID_STATUS_CONDITION_TYPES = ["from", "to", "in", "been", "never"]
# Keywords that name which statuses a search reaches: every open one (the
# default), every closed one, or any at all. They AND with the transition
# conditions like any other keyword ("any+in:Resolved").
STATUS_SCOPE_KEYWORDS = ["open", "closed", "any"]
STATUS_SCOPE_DEFAULT = "open"
# Special keywords that don't require a value
VALID_STATUS_KEYWORDS = ["raised", "lowered"] + STATUS_SCOPE_KEYWORDS
# Valid direction modifiers for "from" patterns
VALID_STATUS_DIRECTIONS = ["raised", "lowered"]

# Fallback status ordering for comparison and workflow progression
# Lower number = earlier in the workflow
# Used to determine if status was "raised" (progressed forward) or "lowered" (moved backward)
# This is only used if the API call to maniphest.querystatuses fails
FALLBACK_STATUS_ORDER = {
    "open": 0,
    "blocked": 1,
    "wontfix": 2,
    "invalid": 3,
    "duplicate": 4,
    "resolved": 5,
}

# Closed status keys, used only if maniphest.querystatuses cannot be read.
# Phorge's defaults; "spite" is one of them.
FALLBACK_CLOSED_STATUSES = ["resolved", "wontfix", "invalid", "duplicate", "spite"]


def closed_status_names(api_response=None):
    """The lowercased keys and display names of every closed status.

    maniphest.querystatuses lists closed statuses by key ("wontfix") while a
    task reports its status by name ("Wontfix"), and a custom status can make
    the two differ by more than case, so both are collected.

    Parameters
    ----------
    api_response : dict, optional
        Full response from maniphest.querystatuses

    Returns
    -------
    set
        Lowercased closed status keys and names
    """
    if not api_response:
        return set(FALLBACK_CLOSED_STATUSES)

    closed = api_response.get("closedStatuses") or {}
    keys = closed.values() if isinstance(closed, dict) else closed
    status_map = api_response.get("statusMap") or {}

    names = set()
    for key in keys:
        names.add(str(key).lower())
        if key in status_map:
            names.add(str(status_map[key]).lower())
    return names


def _build_status_order_from_api(api_response):
    """
    Build status order mapping from API response.

    The maniphest.querystatuses API returns:
    - openStatuses: list of open status keys
    - closedStatuses: dict of closed status values
    - statusMap: dict mapping status keys to display names

    Parameters
    ----------
    api_response : dict
        Response from maniphest.querystatuses API

    Returns
    -------
    dict
        Mapping of status name (lowercase) to order number
    """
    if not api_response:
        return FALLBACK_STATUS_ORDER

    order_map = {}

    open_status_keys = api_response.get("openStatuses", [])
    closed_status_values = api_response.get("closedStatuses", {})
    status_map = api_response.get("statusMap", {})

    current_order = 0

    for status_key in open_status_keys:
        status_name = status_map.get(status_key, status_key)
        order_map[status_name.lower()] = current_order
        current_order += 1

    for status_key in closed_status_values.values():
        status_name = status_map.get(status_key, status_key)
        order_map[status_name.lower()] = current_order
        current_order += 1

    return order_map


def get_status_order(status_name, api_response=None):
    """
    Get the numeric order of a status for comparison.

    Parameters
    ----------
    status_name : str
        Status name (case-insensitive)
    api_response : dict, optional
        Full response from maniphest.querystatuses API.
        If not provided, uses fallback ordering.

    Returns
    -------
    int or None
        Status order number, or None if not found
    """
    if not status_name:
        return None

    if api_response:
        order_map = _build_status_order_from_api(api_response)
    else:
        order_map = FALLBACK_STATUS_ORDER

    return order_map.get(status_name.lower())


class StatusPattern:
    """
    Represents a single status pattern with AND conditions.

    A pattern can have multiple conditions that must all match (AND logic).
    Multiple patterns can be combined with OR logic.
    """

    def __init__(self, conditions, api_response=None):
        """
        Parameters
        ----------
        conditions : list
            List of condition dicts, each with keys like:
            {"type": "from", "status": "Open", "direction": "raised"}
        api_response : dict, optional
            Full response from maniphest.querystatuses API for ordering
        """
        self.conditions = conditions
        self.api_response = api_response

    def __str__(self):
        """Return string representation of the pattern."""
        parts = []
        for cond in self.conditions:
            cond_type = cond.get("type", "")
            negated = cond.get("negated", False)
            prefix = "not:" if negated else ""

            if cond_type in VALID_STATUS_KEYWORDS:
                parts.append(f"{prefix}{cond_type}")
            else:
                status = cond.get("status", "")
                direction = cond.get("direction")
                if direction:
                    parts.append(f"{prefix}{cond_type}:{status}:{direction}")
                else:
                    parts.append(f"{prefix}{cond_type}:{status}")
        return "+".join(parts)

    def matches(self, status_transactions, current_status):
        """
        Check if all conditions in this pattern match (AND logic).

        Parameters
        ----------
        status_transactions : list
            List of status change transactions for a task
        current_status : str or None
            Current status name

        Returns
        -------
        bool
            True if all conditions match, False otherwise
        """
        for condition in self.conditions:
            if not self._matches_condition(
                condition, status_transactions, current_status
            ):
                return False
        return True

    def _matches_condition(self, condition, status_transactions, current_status):
        """Check if a single condition matches."""
        condition_type = condition.get("type")

        if condition_type == "from":
            result = self._matches_from(condition, status_transactions)
        elif condition_type == "to":
            result = self._matches_to(condition, status_transactions)
        elif condition_type == "in":
            result = self._matches_current(condition, current_status)
        elif condition_type == "been":
            result = self._matches_been(condition, status_transactions)
        elif condition_type == "never":
            result = self._matches_never(condition, status_transactions)
        elif condition_type == "raised":
            result = self._matches_raised(status_transactions)
        elif condition_type == "lowered":
            result = self._matches_lowered(status_transactions)
        elif condition_type in STATUS_SCOPE_KEYWORDS:
            result = self._matches_scope(condition_type, current_status)
        else:
            log.warning(f"Unknown condition type: {condition_type}")
            result = False

        if condition.get("negated"):
            result = not result

        return result

    def _matches_scope(self, scope, current_status):
        """Check whether the current status is within a scope keyword."""
        if scope == "any":
            return True
        if not current_status:
            return False
        is_closed = current_status.lower() in closed_status_names(self.api_response)
        return is_closed if scope == "closed" else not is_closed

    def _matches_from(self, condition, status_transactions):
        """Match 'from:STATUS[:direction]' pattern."""
        target_status = condition.get("status")
        direction = condition.get("direction")

        for trans in status_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            if old_value.lower() == target_status.lower():
                if direction is None:
                    return True

                old_order = get_status_order(old_value, self.api_response)
                new_order = get_status_order(new_value, self.api_response)

                if old_order is not None and new_order is not None:
                    if direction == "raised" and new_order > old_order:
                        return True
                    elif direction == "lowered" and new_order < old_order:
                        return True

        return False

    def _matches_to(self, condition, status_transactions):
        """Match 'to:STATUS' pattern."""
        target_status = condition.get("status")

        for trans in status_transactions:
            new_value = trans.get("newValue")

            if new_value is None:
                continue

            if new_value.lower() == target_status.lower():
                return True

        return False

    def _matches_current(self, condition, current_status):
        """Match 'in:STATUS' pattern."""
        target_status = condition.get("status")
        if not current_status:
            return False
        return current_status.lower() == target_status.lower()

    def _matches_been(self, condition, status_transactions):
        """Match 'been:STATUS' pattern - task was at status at any point."""
        target_status = condition.get("status")

        for trans in status_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            for value in [old_value, new_value]:
                if value and value.lower() == target_status.lower():
                    return True

        return False

    def _matches_never(self, condition, status_transactions):
        """Match 'never:STATUS' pattern - task was never at status."""
        target_status = condition.get("status")

        for trans in status_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            for value in [old_value, new_value]:
                if value and value.lower() == target_status.lower():
                    return False

        return True

    def _matches_raised(self, status_transactions):
        """Match 'raised' pattern - status progressed forward."""
        for trans in status_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            old_order = get_status_order(old_value, self.api_response)
            new_order = get_status_order(new_value, self.api_response)

            if old_order is not None and new_order is not None:
                if new_order > old_order:
                    return True

        return False

    def _matches_lowered(self, status_transactions):
        """Match 'lowered' pattern - status moved backward."""
        for trans in status_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            old_order = get_status_order(old_value, self.api_response)
            new_order = get_status_order(new_value, self.api_response)

            if old_order is not None and new_order is not None:
                if new_order < old_order:
                    return True

        return False


def _parse_single_condition(condition_str):
    """
    Parse a single condition string.

    Parameters
    ----------
    condition_str : str
        Condition like "from:Open:raised", "in:Resolved", "raised", "lowered"
        Can be prefixed with "not:" to negate: "not:in:Open", "not:raised"

    Returns
    -------
    dict
        Condition dict with keys like {"type": "from", "status": "Open", "direction": "raised"}
        May include {"negated": True} if prefixed with "not:"

    Raises
    ------
    PhabfiveException
        If condition syntax is invalid
    """
    negated, condition_str = parse_negation_prefix(condition_str)

    parts_info = parse_condition_parts(
        condition_str,
        VALID_STATUS_CONDITION_TYPES,
        VALID_STATUS_KEYWORDS,
        "status",
    )

    if parts_info.get("is_keyword"):
        result = {"type": parts_info["type"]}
        if negated:
            result["negated"] = True
        return result

    result = {"type": parts_info["type"], "status": parts_info["value"]}

    direction = parse_direction(
        parts_info, condition_str, VALID_STATUS_DIRECTIONS, "status"
    )
    if direction:
        result["direction"] = direction

    if negated:
        result["negated"] = True

    return result


def parse_status_patterns(patterns_str, api_response=None):
    """
    Parse status pattern string into list of StatusPattern objects.

    Supports:
    - Comma (,) for OR logic between patterns
    - Plus (+) for AND logic within a pattern

    Parameters
    ----------
    patterns_str : str
        Pattern string like "from:Open:raised+in:Resolved,to:Closed"
    api_response : dict, optional
        Full response from maniphest.querystatuses API for ordering

    Returns
    -------
    list
        List of StatusPattern objects

    Raises
    ------
    PhabfiveException
        If pattern syntax is invalid

    Examples
    --------
    >>> parse_status_patterns("from:Open:raised")
    [StatusPattern([{"type": "from", "status": "Open", "direction": "raised"}])]

    >>> parse_status_patterns("from:Open+in:Resolved,to:Closed")
    [StatusPattern([from:Open, in:Resolved]), StatusPattern([to:Closed])]
    """
    groups = split_pattern_groups(patterns_str, "status")

    patterns = []
    for and_conditions in groups:
        conditions = [_parse_single_condition(cond) for cond in and_conditions]
        patterns.append(StatusPattern(conditions, api_response))

    return patterns


def _group_scope(pattern, default):
    """The statuses one AND group can match, as a subset of {open, closed}.

    A group that names no scope keyword gets the default. Keywords narrow:
    "open+closed" can match nothing, "not:open" is "closed".
    """
    everything = {"open", "closed"}
    scope = set(everything)
    named = False

    for condition in pattern.conditions:
        keyword = condition.get("type")
        if keyword not in STATUS_SCOPE_KEYWORDS:
            continue
        named = True
        reach = everything if keyword == "any" else {keyword}
        if condition.get("negated"):
            reach = everything - reach
        scope &= reach

    if not named:
        return everything if default == "any" else {default}, False
    return scope, True


def resolve_status_scope(patterns, default=STATUS_SCOPE_DEFAULT):
    """Decide which statuses to ask the server for, from parsed --status patterns.

    The scope keywords are what keeps a status search cheap: the server is
    asked only for the statuses some group can match, so a transition
    pattern on its own still costs no history fetches for closed tasks.

    - A group without a scope keyword reaches ``default`` - open, unless the
      caller widened it (the deprecated --all makes it "any").
    - The server scope is the union of what the groups reach.
    - Groups made of nothing but scope keywords are answered by the server
      alone, so when every group is like that no pattern is left to check
      and no task history is fetched.
    - Otherwise a group that reaches less than the server scope gets its
      default made explicit, so the client still enforces it when another
      group widened the fetch (``been:Blocked,closed+in:Resolved``).

    Parameters
    ----------
    patterns : list or None
        StatusPattern objects from parse_status_patterns
    default : str
        Scope of a group that names none: "open" or "any"

    Returns
    -------
    tuple
        (scope, patterns): scope is "open", "closed" or "any"; patterns is
        the list still to check on the client, or None
    """
    if not patterns:
        return default, patterns

    reached = set()
    groups = []
    for pattern in patterns:
        scope, named = _group_scope(pattern, default)
        reached |= scope
        groups.append((pattern, scope, named))

    if reached == {"open"}:
        server_scope = "open"
    elif reached == {"closed"}:
        server_scope = "closed"
    else:
        # Both, or nothing at all (a self-contradicting "open+closed"):
        # fetch everything and let the client reject what cannot match
        server_scope = "any"

    only_keywords = all(
        all(c.get("type") in STATUS_SCOPE_KEYWORDS for c in p.conditions)
        for p in patterns
    )
    if only_keywords and reached:
        return server_scope, None

    remaining = []
    for pattern, scope, named in groups:
        if not named and default != "any" and server_scope != default:
            pattern = StatusPattern(
                pattern.conditions + [{"type": default}], pattern.api_response
            )
        remaining.append(pattern)

    return server_scope, remaining


def unreachable_conditions(patterns, default=STATUS_SCOPE_DEFAULT, api_response=None):
    """Name the ``in:`` conditions that can never match within their group's scope.

    ``--status=in:Resolved`` asks for tasks currently Resolved among open
    tasks, which is none - the classic way to get an empty result and no
    explanation. Only a plain ``in:`` is judged; the other conditions are
    about history and can legitimately name a status outside the scope.

    Returns
    -------
    list
        (condition string, scope) pairs, one per unreachable condition
    """
    closed = closed_status_names(api_response)
    found = []

    for pattern in patterns or []:
        scope, _ = _group_scope(pattern, default)
        for condition in pattern.conditions:
            if condition.get("type") != "in" or condition.get("negated"):
                continue
            is_closed = str(condition.get("status", "")).lower() in closed
            if ("closed" if is_closed else "open") not in scope:
                found.append((f"in:{condition.get('status')}", "/".join(sorted(scope))))

    return found
