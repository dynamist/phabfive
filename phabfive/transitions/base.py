# -*- coding: utf-8 -*-

"""
Shared utilities for transition pattern parsing.

This module provides common parsing logic used by column, status, and priority
transition modules.
"""

from phabfive.exceptions import PhabfiveException


def parse_negation_prefix(condition_str):
    """
    Check for and strip 'not:' prefix from condition string.

    Parameters
    ----------
    condition_str : str
        Condition string, possibly prefixed with 'not:'

    Returns
    -------
    tuple
        (negated: bool, stripped_condition: str)
    """
    condition_str = condition_str.strip()
    if condition_str.startswith("not:"):
        return True, condition_str[4:].strip()
    return False, condition_str


def parse_condition_parts(
    condition_str, valid_condition_types, valid_keywords, entity_name
):
    """
    Parse a condition string into its component parts.

    Parameters
    ----------
    condition_str : str
        Condition string like "from:Value:direction" or "keyword"
    valid_condition_types : list
        Valid condition types (e.g., ["from", "to", "in", "been", "never"])
    valid_keywords : list
        Valid keywords that don't require values (e.g., ["raised", "lowered"])
    entity_name : str
        Name of entity for error messages (e.g., "column", "status", "priority")

    Returns
    -------
    dict or None
        For keywords: {"type": keyword}
        For conditions: {"type": type, "parts": [remaining parts]}
        Returns None if it's a keyword match

    Raises
    ------
    PhabfiveException
        If condition syntax is invalid
    """
    # Check for special keywords without parameters
    if condition_str in valid_keywords:
        return {"type": condition_str, "is_keyword": True}

    # Patterns with parameters: type:value or type:value:direction
    if ":" not in condition_str:
        all_types = valid_condition_types + valid_keywords
        raise PhabfiveException(
            f"Invalid {entity_name} condition syntax: '{condition_str}'. "
            f"Expected format: TYPE:{entity_name.upper()} (e.g., 'in:Value', 'not:in:Done'). "
            f"Valid types: {', '.join(all_types)}"
        )

    parts = condition_str.split(":", 2)  # Split into max 3 parts
    condition_type = parts[0].strip()

    if condition_type not in valid_condition_types:
        all_types = valid_condition_types + valid_keywords
        raise PhabfiveException(
            f"Invalid {entity_name} condition type: '{condition_type}'. "
            f"Valid types: {', '.join(all_types)}"
        )

    if len(parts) < 2:
        raise PhabfiveException(
            f"Missing {entity_name} name for condition: '{condition_str}'"
        )

    value = parts[1].strip()
    if not value:
        raise PhabfiveException(
            f"Empty {entity_name} name in condition: '{condition_str}'"
        )

    return {
        "type": condition_type,
        "value": value,
        "extra_parts": parts[2:] if len(parts) > 2 else [],
        "is_keyword": False,
    }


def parse_direction(parts_info, condition_str, valid_directions, entity_name):
    """
    Parse optional direction from condition parts.

    Parameters
    ----------
    parts_info : dict
        Result from parse_condition_parts
    condition_str : str
        Original condition string for error messages
    valid_directions : list
        Valid direction values
    entity_name : str
        Name of entity for error messages

    Returns
    -------
    str or None
        Direction if present and valid, None otherwise

    Raises
    ------
    PhabfiveException
        If direction is invalid or used with wrong condition type
    """
    if not parts_info.get("extra_parts"):
        return None

    if parts_info["type"] != "from":
        raise PhabfiveException(
            f"Direction modifier only allowed for 'from' patterns, got: '{condition_str}'"
        )

    direction = parts_info["extra_parts"][0].strip()
    if direction not in valid_directions:
        raise PhabfiveException(
            f"Invalid direction: '{direction}'. "
            f"Valid directions: {', '.join(valid_directions)}"
        )

    return direction


def split_pattern_groups(patterns_str, entity_name):
    """
    Split pattern string into OR groups and AND conditions.

    Parameters
    ----------
    patterns_str : str
        Pattern string like "from:A+in:B,to:C"
    entity_name : str
        Name of entity for error messages

    Returns
    -------
    list
        List of lists, where each inner list contains AND conditions
        e.g., [["from:A", "in:B"], ["to:C"]]

    Raises
    ------
    PhabfiveException
        If pattern string is empty
    """
    if not patterns_str or not patterns_str.strip():
        raise PhabfiveException(f"Empty {entity_name} pattern")

    result = []

    # Split by comma for OR groups
    or_groups = patterns_str.split(",")

    for or_group in or_groups:
        or_group = or_group.strip()
        if not or_group:
            continue

        # Split by plus for AND conditions
        and_parts = [p.strip() for p in or_group.split("+") if p.strip()]

        if and_parts:
            result.append(and_parts)

    if not result:
        raise PhabfiveException(f"No valid {entity_name} patterns found")

    return result
