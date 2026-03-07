# -*- coding: utf-8 -*-

"""
Transitions package for task transition pattern matching.

This package provides pattern matching for various types of task transitions:
- Column transitions (workboard column movements)
- Status transitions (task status changes)
- Priority transitions (task priority changes)

The package is structured into submodules:
- base: Shared parsing utilities
- column: ColumnPattern and column transition parsing
- status: StatusPattern and status transition parsing
- priority: PriorityPattern and priority transition parsing
"""

from phabfive.transitions.column import (
    ColumnPattern,
    parse_column_patterns,
    VALID_COLUMN_CONDITION_TYPES,
    VALID_COLUMN_DIRECTIONS,
    VALID_COLUMN_KEYWORDS,
)
from phabfive.transitions.priority import (
    PRIORITY_ORDER,
    PriorityPattern,
    get_priority_order,
    parse_priority_patterns,
    VALID_PRIORITY_CONDITION_TYPES,
    VALID_PRIORITY_DIRECTIONS,
    VALID_PRIORITY_KEYWORDS,
)
from phabfive.transitions.status import (
    FALLBACK_STATUS_ORDER,
    StatusPattern,
    get_status_order,
    parse_status_patterns,
    VALID_STATUS_CONDITION_TYPES,
    VALID_STATUS_DIRECTIONS,
    VALID_STATUS_KEYWORDS,
)

__all__ = [
    # Column transitions
    "ColumnPattern",
    "parse_column_patterns",
    "VALID_COLUMN_CONDITION_TYPES",
    "VALID_COLUMN_DIRECTIONS",
    "VALID_COLUMN_KEYWORDS",
    # Status transitions
    "StatusPattern",
    "parse_status_patterns",
    "get_status_order",
    "VALID_STATUS_CONDITION_TYPES",
    "VALID_STATUS_DIRECTIONS",
    "VALID_STATUS_KEYWORDS",
    "FALLBACK_STATUS_ORDER",
    # Priority transitions
    "PriorityPattern",
    "parse_priority_patterns",
    "get_priority_order",
    "VALID_PRIORITY_CONDITION_TYPES",
    "VALID_PRIORITY_DIRECTIONS",
    "VALID_PRIORITY_KEYWORDS",
    "PRIORITY_ORDER",
]
