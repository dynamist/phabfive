# -*- coding: utf-8 -*-

"""Utility functions for Maniphest operations."""

import datetime
import json
import logging
import re
import time
from collections.abc import Mapping
from typing import Optional

from jinja2 import Environment, Template, meta

from phabfive.exceptions import PhabfiveDataException

log = logging.getLogger(__name__)


def days_ago_to_timestamp(days):
    """
    Convert days into a UNIX timestamp.
    """
    seconds = int(days) * 24 * 3600
    return int(time.time()) - seconds


def format_timestamp(timestamp):
    """
    Convert UNIX timestamp to ISO 8601 string (readable time format).
    """
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_time_with_unit(time_value):
    """
    Parse time value with optional unit suffix.

    Supports the following time units:
    - h: hours
    - d: days (default when no unit specified)
    - w: weeks (7 days)
    - m: months (30 days)
    - y: years (365 days)

    Parameters
    ----------
    time_value : str, int, float, or None
        Time value with optional unit suffix.
        Examples: "1w", "2m", "7d", "7", 7

    Returns
    -------
    float or None
        Number of days as a float, or None if input is None.

    Raises
    ------
    ValueError
        If the format is invalid or the unit is not recognized.

    Examples
    --------
    >>> parse_time_with_unit("1w")
    7.0
    >>> parse_time_with_unit("2m")
    60.0
    >>> parse_time_with_unit("7")
    7.0
    >>> parse_time_with_unit(7)
    7.0
    >>> parse_time_with_unit("1h")
    0.041666666666666664
    """
    if time_value is None:
        return None

    # Convert to string for parsing
    time_str = str(time_value).strip()

    if not time_str:
        raise ValueError("Time value cannot be empty")

    # Define unit conversions to days
    unit_to_days = {
        "h": 1 / 24,  # hours to days
        "d": 1,  # days
        "w": 7,  # weeks to days
        "m": 30,  # months to days (approximate)
        "y": 365,  # years to days (approximate)
    }

    # Try to parse as a plain number first (backward compatibility)
    try:
        # If it's just a number, treat as days
        days = float(time_str)
        if days < 0:
            raise ValueError(f"Time value cannot be negative: '{time_value}'")
        return days
    except ValueError as e:
        # If it's a negative value error, re-raise it
        if "cannot be negative" in str(e):
            raise
        # Not a plain number, try to parse with unit suffix
        pass

    # Extract numeric part and unit suffix
    match = re.match(r"^(-?[0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)$", time_str)
    if not match:
        raise ValueError(
            f"Invalid time format: '{time_value}'. "
            f"Expected format: NUMBER[UNIT] where UNIT is one of: h, d, w, m, y. "
            f"Examples: '7d', '1w', '2m', '1y', '12h', or just '7' (defaults to days)"
        )

    numeric_part = match.group(1)
    unit = match.group(2).lower()

    if unit not in unit_to_days:
        valid_units = ", ".join(sorted(unit_to_days.keys()))
        raise ValueError(f"Invalid time unit: '{unit}'. Valid units are: {valid_units}")

    try:
        numeric_value = float(numeric_part)
    except ValueError:
        raise ValueError(f"Invalid numeric value: '{numeric_part}'")

    if numeric_value < 0:
        raise ValueError(f"Time value cannot be negative: '{time_value}'")

    # Convert to days
    days = numeric_value * unit_to_days[unit]
    return days


def extract_variable_dependencies(template_str: str) -> set[str]:
    """
    Extract variable names referenced in a Jinja2 template string.
    """
    try:
        env = Environment()
        ast = env.parse(template_str)
        return meta.find_undeclared_variables(ast)
    except Exception as e:
        log.warning(f"Failed to parse template '{template_str}': {e}")
        return set()


def build_dependency_graph(variables: Mapping[str, object]) -> dict[str, set[str]]:
    """
    Build a dependency graph mapping each variable to its dependencies.
    """
    graph: dict[str, set[str]] = {}

    for var_name, var_value in variables.items():
        if isinstance(var_value, str):
            # Extract dependencies and filter to only include string variables
            # (non-strings don't need rendering, so no ordering constraint)
            dependencies = extract_variable_dependencies(var_value)
            graph[var_name] = {
                dep
                for dep in dependencies
                if dep in variables and isinstance(variables[dep], str)
            }
        else:
            # Non-string values have no dependencies
            graph[var_name] = set()

    return graph


def detect_circular_dependencies(graph: dict[str, set[str]]) -> tuple[bool, list[str]]:
    """
    Detect circular dependencies using depth-first search.
    Returns (has_cycle, cycle_path) tuple.
    """
    visited: set[str] = set()
    recursion_stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> Optional[list[str]]:
        visited.add(node)
        recursion_stack.add(node)
        path.append(node)

        for dependency in graph.get(node, set()):
            if dependency not in visited:
                cycle = dfs(dependency)
                if cycle is not None:
                    return cycle
            elif dependency in recursion_stack:
                # Found a cycle - build the cycle path
                cycle_start_idx = path.index(dependency)
                cycle_path = path[cycle_start_idx:] + [dependency]
                return cycle_path

        _ = path.pop()
        recursion_stack.remove(node)
        return None

    for node in graph:
        if node not in visited:
            result = dfs(node)
            if result is not None:
                return (True, result)

    return (False, [])


def topological_sort(graph: dict[str, set[str]]) -> list[str]:
    """
    Perform topological sort using DFS.
    Returns variables in dependency order (dependencies before dependents).
    """
    visited: set[str] = set()
    result: list[str] = []

    def dfs(node: str) -> None:
        visited.add(node)

        # Visit all dependencies first
        for dependency in graph.get(node, set()):
            if dependency not in visited:
                dfs(dependency)

        # Add current node after all dependencies
        result.append(node)

    for node in graph:
        if node not in visited:
            dfs(node)

    return result


def render_variables_with_dependency_resolution(
    variables: Mapping[str, object],
) -> dict[str, object]:
    """
    Render Jinja2 template variables with proper dependency resolution.

    Analyzes variable dependencies, detects circular references, performs topological
    sorting, and renders variables in the correct order so all dependencies are
    resolved before being referenced.

    Raises
    ------
    PhabfiveDataException
        If circular dependencies are detected in the variable definitions.
    """
    log.debug("Building dependency graph for variables")
    graph = build_dependency_graph(variables)
    log.debug(
        f"Dependency graph: {json.dumps({k: list(v) for k, v in graph.items()}, indent=2)}"
    )

    # Detect circular dependencies
    has_cycle, cycle_path = detect_circular_dependencies(graph)
    if has_cycle:
        cycle_str = " → ".join(cycle_path)
        raise PhabfiveDataException(
            f"Circular reference detected in variables: {cycle_str}"
        )

    # Sort variables in dependency order
    sorted_vars = topological_sort(graph)
    log.debug(f"Topologically sorted variables: {sorted_vars}")

    # Render variables in order
    rendered = dict(variables)
    for var_name in sorted_vars:
        var_value = rendered[var_name]
        if isinstance(var_value, str):
            rendered[var_name] = Template(var_value).render(rendered)
            log.debug(f"Rendered variable '{var_name}': {rendered[var_name]}")

    return rendered
