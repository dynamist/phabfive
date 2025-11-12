# -*- coding: utf-8 -*-

# python std lib
import logging

# phabfive imports
from phabfive.exceptions import PhabfiveException

log = logging.getLogger(__name__)

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
        # All conditions must match for the pattern to match
        for condition in self.conditions:
            if not self._matches_condition(
                condition, priority_transactions, current_priority
            ):
                return False
        return True

    def _matches_condition(self, condition, priority_transactions, current_priority):
        """Check if a single condition matches."""
        condition_type = condition.get("type")

        # Determine the match result based on condition type
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

        # Apply negation if the condition has the "not:" prefix
        if condition.get("negated"):
            result = not result

        return result

    def _matches_from(self, condition, priority_transactions):
        """Match 'from:PRIORITY[:direction]' pattern."""
        target_priority = condition.get("priority")
        direction = condition.get("direction")  # None, "raised", or "lowered"

        for trans in priority_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            # Check if old priority matches target (case-insensitive)
            if old_value.lower() == target_priority.lower():
                # If no direction specified, it's a match
                if direction is None:
                    return True

                # Check direction
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

            # Check both old and new values
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

            # Check both old and new values
            for value in [old_value, new_value]:
                if value and value.lower() == target_priority.lower():
                    return False  # Found the priority, so it's not "never"

        return True  # Never found the priority

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
                if new_order < old_order:  # Lower number = higher priority
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
                if new_order > old_order:  # Higher number = lower priority
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
    condition_str = condition_str.strip()

    # Check for not: prefix
    negated = False
    if condition_str.startswith("not:"):
        negated = True
        condition_str = condition_str[4:].strip()  # Strip "not:" prefix

    # Special keywords without parameters
    if condition_str == "raised":
        result = {"type": "raised"}
        if negated:
            result["negated"] = True
        return result
    elif condition_str == "lowered":
        result = {"type": "lowered"}
        if negated:
            result["negated"] = True
        return result

    # Patterns with parameters: type:value or type:value:direction
    if ":" not in condition_str:
        raise PhabfiveException(f"Invalid priority condition syntax: '{condition_str}'")

    parts = condition_str.split(":", 2)  # Split into max 3 parts
    condition_type = parts[0].strip()

    if condition_type not in ["from", "to", "in", "been", "never"]:
        raise PhabfiveException(
            f"Invalid priority condition type: '{condition_type}'. "
            f"Valid types: from, to, in, been, never, raised, lowered"
        )

    if len(parts) < 2:
        raise PhabfiveException(
            f"Missing priority name for condition: '{condition_str}'"
        )

    priority_name = parts[1].strip()
    if not priority_name:
        raise PhabfiveException(f"Empty priority name in condition: '{condition_str}'")

    result = {"type": condition_type, "priority": priority_name}

    # Handle optional direction for 'from' patterns
    if len(parts) == 3:
        if condition_type != "from":
            raise PhabfiveException(
                f"Direction modifier only allowed for 'from' patterns, got: '{condition_str}'"
            )
        direction = parts[2].strip()
        if direction not in ["raised", "lowered"]:
            raise PhabfiveException(
                f"Invalid direction: '{direction}'. Must be 'raised' or 'lowered'"
            )
        result["direction"] = direction

    # Add negated flag if not: prefix was present
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
    if not patterns_str or not patterns_str.strip():
        raise PhabfiveException("Empty priority pattern")

    patterns = []

    # Split by comma for OR groups
    or_groups = patterns_str.split(",")

    for or_group in or_groups:
        or_group = or_group.strip()
        if not or_group:
            continue

        conditions = []

        # Split by plus for AND conditions
        and_parts = or_group.split("+")

        for and_part in and_parts:
            and_part = and_part.strip()
            if not and_part:
                continue

            condition = _parse_single_condition(and_part)
            conditions.append(condition)

        if conditions:
            patterns.append(PriorityPattern(conditions))

    if not patterns:
        raise PhabfiveException("No valid priority patterns found")

    return patterns
