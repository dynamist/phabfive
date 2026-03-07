# -*- coding: utf-8 -*-

"""
Column transition pattern matching for workboard columns.

This module provides pattern matching for task movements between workboard columns,
supporting conditions like 'from:Inbox', 'to:Done', 'in:Blocked', 'backward', 'forward'.
"""

import logging

from phabfive.transitions.base import (
    parse_condition_parts,
    parse_direction,
    parse_negation_prefix,
    split_pattern_groups,
)

log = logging.getLogger(__name__)

# Valid condition types for column transition patterns
# Types that require a column value (e.g., "in:Inbox")
VALID_COLUMN_CONDITION_TYPES = ["from", "to", "in", "been", "never"]
# Special keywords that don't require a value
VALID_COLUMN_KEYWORDS = ["backward", "forward"]
# Valid direction modifiers for "from" patterns
VALID_COLUMN_DIRECTIONS = ["forward", "backward"]


class ColumnPattern:
    """
    Represents a single transition pattern with AND conditions.

    A pattern can have multiple conditions that must all match (AND logic).
    Multiple patterns can be combined with OR logic.
    """

    def __init__(self, conditions):
        """
        Parameters
        ----------
        conditions : list
            List of condition dicts, each with keys like:
            {"type": "from", "column": "In Progress", "direction": "forward"}
        """
        self.conditions = conditions

    def __str__(self):
        """Return string representation of the pattern."""
        parts = []
        for cond in self.conditions:
            cond_type = cond.get("type", "")
            negated = cond.get("negated", False)
            prefix = "not:" if negated else ""

            if cond_type in ("backward", "forward"):
                parts.append(f"{prefix}{cond_type}")
            else:
                column = cond.get("column", "")
                direction = cond.get("direction")
                if direction:
                    parts.append(f"{prefix}{cond_type}:{column}:{direction}")
                else:
                    parts.append(f"{prefix}{cond_type}:{column}")
        return "+".join(parts)

    def matches(self, task_transactions, current_column, column_info):
        """
        Check if all conditions in this pattern match (AND logic).

        Parameters
        ----------
        task_transactions : list
            List of column change transactions for a task
        current_column : str or None
            Current column name the task is in
        column_info : dict
            Mapping of column PHID to {"name": str, "sequence": int}

        Returns
        -------
        bool
            True if all conditions match, False otherwise
        """
        for condition in self.conditions:
            if not self._matches_condition(
                condition, task_transactions, current_column, column_info
            ):
                return False
        return True

    def _matches_condition(
        self, condition, task_transactions, current_column, column_info
    ):
        """Check if a single condition matches."""
        condition_type = condition.get("type")

        if condition_type == "from":
            result = self._matches_from(condition, task_transactions, column_info)
        elif condition_type == "to":
            result = self._matches_to(condition, task_transactions, column_info)
        elif condition_type == "in":
            result = self._matches_current(condition, current_column)
        elif condition_type == "been":
            result = self._matches_been(condition, task_transactions, column_info)
        elif condition_type == "never":
            result = self._matches_never(condition, task_transactions, column_info)
        elif condition_type == "backward":
            result = self._matches_backward(task_transactions, column_info)
        elif condition_type == "forward":
            result = self._matches_forward(task_transactions, column_info)
        else:
            log.warning(f"Unknown condition type: {condition_type}")
            result = False

        if condition.get("negated"):
            result = not result

        return result

    def _matches_from(self, condition, task_transactions, column_info):
        """Match 'from:COLUMN[:direction]' pattern."""
        target_column = condition.get("column")
        direction = condition.get("direction")

        for trans in task_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if not old_value or not new_value:
                continue

            old_column_phid = old_value[1] if len(old_value) > 1 else None
            new_column_phid = new_value[1] if len(new_value) > 1 else None

            if not old_column_phid or not new_column_phid:
                continue

            old_col_info = column_info.get(old_column_phid)
            new_col_info = column_info.get(new_column_phid)

            if not old_col_info:
                continue

            if old_col_info["name"] == target_column:
                if direction is None:
                    return True

                if new_col_info:
                    if (
                        direction == "forward"
                        and new_col_info["sequence"] > old_col_info["sequence"]
                    ):
                        return True
                    elif (
                        direction == "backward"
                        and new_col_info["sequence"] < old_col_info["sequence"]
                    ):
                        return True

        return False

    def _matches_to(self, condition, task_transactions, column_info):
        """Match 'to:COLUMN' pattern."""
        target_column = condition.get("column")

        for trans in task_transactions:
            new_value = trans.get("newValue")

            if not new_value:
                continue

            new_column_phid = new_value[1] if len(new_value) > 1 else None
            if not new_column_phid:
                continue

            new_col_info = column_info.get(new_column_phid)
            if new_col_info and new_col_info["name"] == target_column:
                return True

        return False

    def _matches_current(self, condition, current_column):
        """Match 'in:COLUMN' pattern."""
        target_column = condition.get("column")
        return current_column == target_column

    def _matches_been(self, condition, task_transactions, column_info):
        """Match 'been:COLUMN' pattern - task was in column at any point."""
        target_column = condition.get("column")

        for trans in task_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            for value in [old_value, new_value]:
                if not value:
                    continue

                column_phid = value[1] if len(value) > 1 else None
                if not column_phid:
                    continue

                col_info = column_info.get(column_phid)
                if col_info and col_info["name"] == target_column:
                    return True

        return False

    def _matches_never(self, condition, task_transactions, column_info):
        """Match 'never:COLUMN' pattern - task was never in column."""
        target_column = condition.get("column")

        for trans in task_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            for value in [old_value, new_value]:
                if not value:
                    continue

                column_phid = value[1] if len(value) > 1 else None
                if not column_phid:
                    continue

                col_info = column_info.get(column_phid)
                if col_info and col_info["name"] == target_column:
                    return False

        return True

    def _matches_backward(self, task_transactions, column_info):
        """Match 'backward' pattern - any backward movement."""
        for trans in task_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if not old_value or not new_value:
                continue

            old_column_phid = old_value[1] if len(old_value) > 1 else None
            new_column_phid = new_value[1] if len(new_value) > 1 else None

            if not old_column_phid or not new_column_phid:
                continue

            old_col_info = column_info.get(old_column_phid)
            new_col_info = column_info.get(new_column_phid)

            if old_col_info and new_col_info:
                if new_col_info["sequence"] < old_col_info["sequence"]:
                    return True

        return False

    def _matches_forward(self, task_transactions, column_info):
        """Match 'forward' pattern - any forward movement."""
        for trans in task_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if not old_value or not new_value:
                continue

            old_column_phid = old_value[1] if len(old_value) > 1 else None
            new_column_phid = new_value[1] if len(new_value) > 1 else None

            if not old_column_phid or not new_column_phid:
                continue

            old_col_info = column_info.get(old_column_phid)
            new_col_info = column_info.get(new_column_phid)

            if old_col_info and new_col_info:
                if new_col_info["sequence"] > old_col_info["sequence"]:
                    return True

        return False


def _parse_single_condition(condition_str):
    """
    Parse a single condition string.

    Parameters
    ----------
    condition_str : str
        Condition like "from:Up Next:forward", "in:Blocked", "backward", "forward"
        Can be prefixed with "not:" to negate: "not:in:Blocked", "not:backward"

    Returns
    -------
    dict
        Condition dict with keys like {"type": "from", "column": "Up Next", "direction": "forward"}
        May include {"negated": True} if prefixed with "not:"

    Raises
    ------
    PhabfiveException
        If condition syntax is invalid
    """
    negated, condition_str = parse_negation_prefix(condition_str)

    parts_info = parse_condition_parts(
        condition_str,
        VALID_COLUMN_CONDITION_TYPES,
        VALID_COLUMN_KEYWORDS,
        "column",
    )

    if parts_info.get("is_keyword"):
        result = {"type": parts_info["type"]}
        if negated:
            result["negated"] = True
        return result

    result = {"type": parts_info["type"], "column": parts_info["value"]}

    direction = parse_direction(
        parts_info, condition_str, VALID_COLUMN_DIRECTIONS, "column"
    )
    if direction:
        result["direction"] = direction

    if negated:
        result["negated"] = True

    return result


def parse_column_patterns(patterns_str):
    """
    Parse transition pattern string into list of ColumnPattern objects.

    Supports:
    - Comma (,) for OR logic between patterns
    - Plus (+) for AND logic within a pattern

    Parameters
    ----------
    patterns_str : str
        Pattern string like "from:A:forward+in:B,to:C"

    Returns
    -------
    list
        List of ColumnPattern objects

    Raises
    ------
    PhabfiveException
        If pattern syntax is invalid

    Examples
    --------
    >>> parse_column_patterns("from:A:forward")
    [ColumnPattern([{"type": "from", "column": "A", "direction": "forward"}])]

    >>> parse_column_patterns("from:A+in:B,to:C")
    [ColumnPattern([from:A, in:B]), ColumnPattern([to:C])]
    """
    groups = split_pattern_groups(patterns_str, "transition")

    patterns = []
    for and_conditions in groups:
        conditions = [_parse_single_condition(cond) for cond in and_conditions]
        patterns.append(ColumnPattern(conditions))

    return patterns
