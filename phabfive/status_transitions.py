# -*- coding: utf-8 -*-

# python std lib
import logging

# phabfive imports
from phabfive.exceptions import PhabfiveException

log = logging.getLogger(__name__)

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

    # Extract status information from API response
    open_status_keys = api_response.get("openStatuses", [])
    closed_status_values = api_response.get("closedStatuses", {})
    status_map = api_response.get("statusMap", {})

    # Build order: open statuses first (lower numbers), then closed statuses
    current_order = 0

    # Add open statuses
    for status_key in open_status_keys:
        status_name = status_map.get(status_key, status_key)
        order_map[status_name.lower()] = current_order
        current_order += 1

    # Add closed statuses
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

    # Build order map from API response if provided
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

            if cond_type in ("raised", "lowered"):
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
        # All conditions must match for the pattern to match
        for condition in self.conditions:
            if not self._matches_condition(
                condition, status_transactions, current_status
            ):
                return False
        return True

    def _matches_condition(self, condition, status_transactions, current_status):
        """Check if a single condition matches."""
        condition_type = condition.get("type")

        # Determine the match result based on condition type
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
        else:
            log.warning(f"Unknown condition type: {condition_type}")
            result = False

        # Apply negation if the condition has the "not:" prefix
        if condition.get("negated"):
            result = not result

        return result

    def _matches_from(self, condition, status_transactions):
        """Match 'from:STATUS[:direction]' pattern."""
        target_status = condition.get("status")
        direction = condition.get("direction")  # None, "raised", or "lowered"

        for trans in status_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            # Check if old status matches target (case-insensitive)
            if old_value.lower() == target_status.lower():
                # If no direction specified, it's a match
                if direction is None:
                    return True

                # Check direction
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

            # Check both old and new values
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

            # Check both old and new values
            for value in [old_value, new_value]:
                if value and value.lower() == target_status.lower():
                    return False  # Found the status, so it's not "never"

        return True  # Never found the status

    def _matches_raised(self, status_transactions):
        """Match 'raised' pattern - status progressed forward (higher number = further along)."""
        for trans in status_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            old_order = get_status_order(old_value, self.api_response)
            new_order = get_status_order(new_value, self.api_response)

            if old_order is not None and new_order is not None:
                if new_order > old_order:  # Higher number = further along
                    return True

        return False

    def _matches_lowered(self, status_transactions):
        """Match 'lowered' pattern - status moved backward (lower number = earlier stage)."""
        for trans in status_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")

            if old_value is None or new_value is None:
                continue

            old_order = get_status_order(old_value, self.api_response)
            new_order = get_status_order(new_value, self.api_response)

            if old_order is not None and new_order is not None:
                if new_order < old_order:  # Lower number = earlier stage
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
        raise PhabfiveException(f"Invalid status condition syntax: '{condition_str}'")

    parts = condition_str.split(":", 2)  # Split into max 3 parts
    condition_type = parts[0].strip()

    if condition_type not in ["from", "to", "in", "been", "never"]:
        raise PhabfiveException(
            f"Invalid status condition type: '{condition_type}'. "
            f"Valid types: from, to, in, been, never, raised, lowered"
        )

    if len(parts) < 2:
        raise PhabfiveException(f"Missing status name for condition: '{condition_str}'")

    status_name = parts[1].strip()
    if not status_name:
        raise PhabfiveException(f"Empty status name in condition: '{condition_str}'")

    result = {"type": condition_type, "status": status_name}

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
    if not patterns_str or not patterns_str.strip():
        raise PhabfiveException("Empty status pattern")

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
            patterns.append(StatusPattern(conditions, api_response))

    if not patterns:
        raise PhabfiveException("No valid status patterns found")

    return patterns
