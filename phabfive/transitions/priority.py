# -*- coding: utf-8 -*-

"""
Priority transition pattern matching for task priorities.

This module provides pattern matching for task priority changes,
supporting conditions like 'from:High', 'to:Normal', 'in:Low', 'raised', 'lowered'.
"""

import logging

from phabfive.transitions.base import (
    parse_condition_parts,
    parse_direction,
    parse_negation_prefix,
    split_pattern_groups,
)

log = logging.getLogger(__name__)

# Valid condition types for priority patterns
# Types that require a priority value (e.g., "in:High")
VALID_PRIORITY_CONDITION_TYPES = ["from", "to", "in", "been", "never"]
# Special keywords that don't require a value
VALID_PRIORITY_KEYWORDS = ["raised", "lowered"]
# Valid direction modifiers for "from" patterns
VALID_PRIORITY_DIRECTIONS = ["raised", "lowered"]

# Priority ordering for comparison and sorting
# Lower number = higher/more urgent priority
# Used to determine if priority was "raised" (decreased number) or "lowered" (increased number)
# NOTE: This is separate from the API numeric values (100, 90, 80, 50, 25, 0)
PRIORITY_ORDER = {
    "unbreak now!": 0,
    "triage": 1,
    "high": 2,
    "normal": 3,
    "low": 4,
    "wishlist": 5,
}


def get_priority_order(priority_name):
    """
    Get the numeric order of a priority for comparison.

    Parameters
    ----------
    priority_name : str
        Priority name (case-insensitive)

    Returns
    -------
    int or None
        Priority order number, or None if not found
    """
    if not priority_name:
        return None
    return PRIORITY_ORDER.get(priority_name.lower())


class PriorityPattern:
    """
    Represents a single priority pattern with AND conditions.

    A pattern can have multiple conditions that must all match (AND logic).
    Multiple patterns can be combined with OR logic.
    """

    def __init__(self, conditions):
        """
        Parameters
        ----------
        conditions : list
            List of condition dicts, each with keys like:
            {"type": "from", "priority": "High", "direction": "raised"}
        """
        self.conditions = conditions

    def __str__(self):
        """Return string representation of the pattern."""
        parts = []
        for cond in self.conditions:
            cond_type = cond.get("type", "")
            negated = cond.get("negated", False)
            prefix = "not:" if negated else ""

            if cond_type in ("raised", "lowered"):
                parts.append(f"{prefix}{cond_type}")
            else:
                priority = cond.get("priority", "")
                direction = cond.get("direction")
                if direction:
                    parts.append(f"{prefix}{cond_type}:{priority}:{direction}")
                else:
                    parts.append(f"{prefix}{cond_type}:{priority}")
        return "+".join(parts)

    def matches(self, priority_transactions, current_priority):
        """
        Check if all conditions in this pattern match (AND logic).

        Parameters
        ----------
        priority_transactions : list
            List of priority change transactions for a task
        current_priority : str or None
            Current priority name

        Returns
        -------
        bool
            True if all conditions match, False otherwise
        """
        for condition in self.conditions:
            if not self._matches_condition(
                condition, priority_transactions, current_priority
            ):
                return False
        return True

    def _matches_condition(self, condition, priority_transactions, current_priority):
        """Check if a single condition matches."""
        condition_type = condition.get("type")

        if condition_type == "from":
            result = self._matches_from(condition, priority_transactions)
        elif condition_type == "to":
            result = self._matches_to(condition, priority_transactions)
        elif condition_type == "in":
            result = self._matches_current(condition, current_priority)
        elif condition_type == "been":
            result = self._matches_been(condition, priority_transactions)
        elif condition_type == "never":
            result = self._matches_never(condition, priority_transactions)
        elif condition_type == "raised":
            result = self._matches_raised(priority_transactions)
        elif condition_type == "lowered":
            result = self._matches_lowered(priority_transactions)
        else:
            log.warning(f"Unknown condition type: {condition_type}")
            result = False

        if condition.get("negated"):
            result = not result

        return result

    def _matches_from(self, condition, priority_transactions):
        """Match 'from:PRIORITY[:direction]' pattern."""
        target_priority = condition.get("priority")
        direction = condition.get("direction")

        for trans in priority_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            if old_value.lower() == target_priority.lower():
                if direction is None:
                    return True

                old_order = get_priority_order(old_value)
                new_order = get_priority_order(new_value)

                if old_order is not None and new_order is not None:
                    if direction == "raised" and new_order < old_order:
                        return True
                    elif direction == "lowered" and new_order > old_order:
                        return True

        return False

    def _matches_to(self, condition, priority_transactions):
        """Match 'to:PRIORITY' pattern."""
        target_priority = condition.get("priority")

        for trans in priority_transactions:
            new_value = trans.get("newValue")

            if new_value is None:
                continue

            if new_value.lower() == target_priority.lower():
                return True

        return False

    def _matches_current(self, condition, current_priority):
        """Match 'in:PRIORITY' pattern."""
        target_priority = condition.get("priority")
        if not current_priority:
            return False
        return current_priority.lower() == target_priority.lower()

    def _matches_been(self, condition, priority_transactions):
        """Match 'been:PRIORITY' pattern - task was at priority at any point."""
        target_priority = condition.get("priority")

        for trans in priority_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            for value in [old_value, new_value]:
                if value and value.lower() == target_priority.lower():
                    return True

        return False

    def _matches_never(self, condition, priority_transactions):
        """Match 'never:PRIORITY' pattern - task was never at priority."""
        target_priority = condition.get("priority")

        for trans in priority_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            for value in [old_value, new_value]:
                if value and value.lower() == target_priority.lower():
                    return False

        return True

    def _matches_raised(self, priority_transactions):
        """Match 'raised' pattern - any priority increase (lower number = higher priority)."""
        for trans in priority_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            old_order = get_priority_order(old_value)
            new_order = get_priority_order(new_value)

            if old_order is not None and new_order is not None:
                if new_order < old_order:
                    return True

        return False

    def _matches_lowered(self, priority_transactions):
        """Match 'lowered' pattern - any priority decrease (higher number = lower priority)."""
        for trans in priority_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            old_order = get_priority_order(old_value)
            new_order = get_priority_order(new_value)

            if old_order is not None and new_order is not None:
                if new_order > old_order:
                    return True

        return False


def _parse_single_condition(condition_str):
    """
    Parse a single condition string.

    Parameters
    ----------
    condition_str : str
        Condition like "from:High:raised", "in:Normal", "raised", "lowered"
        Can be prefixed with "not:" to negate: "not:in:High", "not:raised"

    Returns
    -------
    dict
        Condition dict with keys like {"type": "from", "priority": "High", "direction": "raised"}
        May include {"negated": True} if prefixed with "not:"

    Raises
    ------
    PhabfiveException
        If condition syntax is invalid
    """
    negated, condition_str = parse_negation_prefix(condition_str)

    parts_info = parse_condition_parts(
        condition_str,
        VALID_PRIORITY_CONDITION_TYPES,
        VALID_PRIORITY_KEYWORDS,
        "priority",
    )

    if parts_info.get("is_keyword"):
        result = {"type": parts_info["type"]}
        if negated:
            result["negated"] = True
        return result

    result = {"type": parts_info["type"], "priority": parts_info["value"]}

    direction = parse_direction(
        parts_info, condition_str, VALID_PRIORITY_DIRECTIONS, "priority"
    )
    if direction:
        result["direction"] = direction

    if negated:
        result["negated"] = True

    return result


def parse_priority_patterns(patterns_str):
    """
    Parse priority pattern string into list of PriorityPattern objects.

    Supports:
    - Comma (,) for OR logic between patterns
    - Plus (+) for AND logic within a pattern

    Parameters
    ----------
    patterns_str : str
        Pattern string like "from:Normal:raised+in:High,to:Unbreak Now!"

    Returns
    -------
    list
        List of PriorityPattern objects

    Raises
    ------
    PhabfiveException
        If pattern syntax is invalid

    Examples
    --------
    >>> parse_priority_patterns("from:Normal:raised")
    [PriorityPattern([{"type": "from", "priority": "Normal", "direction": "raised"}])]

    >>> parse_priority_patterns("from:High+in:Normal,to:Low")
    [PriorityPattern([from:High, in:Normal]), PriorityPattern([to:Low])]
    """
    groups = split_pattern_groups(patterns_str, "priority")

    patterns = []
    for and_conditions in groups:
        conditions = [_parse_single_condition(cond) for cond in and_conditions]
        patterns.append(PriorityPattern(conditions))

    return patterns
