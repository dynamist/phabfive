# -*- coding: utf-8 -*-

"""Task filtering functions for Maniphest operations."""

import logging

from phabfive.maniphest.fetchers import (
    fetch_all_transactions,
    fetch_priority_transactions,
    fetch_task_transactions,
    get_column_info,
    get_current_column,
)

log = logging.getLogger(__name__)


def task_matches_priority_patterns(
    phab, task, task_phid, priority_patterns, priority_map_func, transactions=None
):
    """
    Check if a task matches any of the given priority patterns.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    task : dict
        Task data from maniphest.search
    task_phid : str
        Task PHID
    priority_patterns : list
        List of PriorityPattern objects
    priority_map_func : callable
        Function to get priority map
    transactions : dict, optional
        Pre-fetched transactions dict with keys 'columns', 'priority', 'status'.
        If None, will fetch priority transactions using the old method.

    Returns
    -------
    tuple
        (matches: bool, priority_transactions: list)
        priority_transactions contains all priority change transactions
    """
    if not priority_patterns:
        return (True, [])  # No filtering needed

    # Use pre-fetched transactions if provided, otherwise fetch
    if transactions is not None and "priority" in transactions:
        priority_transactions = transactions["priority"]
    else:
        priority_transactions = fetch_priority_transactions(
            phab, task_phid, priority_map_func
        )

    # Get current priority
    current_priority = task.get("fields", {}).get("priority", {}).get("name")

    # Check if any pattern matches
    for pattern in priority_patterns:
        if pattern.matches(priority_transactions, current_priority):
            return (True, priority_transactions)

    return (False, [])


def task_matches_status_patterns(
    phab, task, task_phid, status_patterns, priority_map_func, transactions=None
):
    """
    Check if a task matches any of the given status patterns.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    task : dict
        Task data from maniphest.search
    task_phid : str
        Task PHID
    status_patterns : list
        List of StatusPattern objects
    priority_map_func : callable
        Function to get priority map (needed for fetch_all_transactions)
    transactions : dict, optional
        Pre-fetched transactions dict with 'status' key.
        If not provided, will fetch status transactions.

    Returns
    -------
    tuple
        (matches: bool, status_transactions: list)
        status_transactions contains all status change transactions
    """
    if not status_patterns:
        return (True, [])  # No filtering needed

    # Use pre-fetched transactions if provided, otherwise fetch
    if transactions is not None and "status" in transactions:
        status_transactions = transactions["status"]
    else:
        # Fetch status transactions using consolidated method
        all_transactions = fetch_all_transactions(
            phab, task_phid, priority_map_func, need_status=True
        )
        status_transactions = all_transactions.get("status", [])

    # Get current status
    current_status = task.get("fields", {}).get("status", {}).get("name")

    # Check if any pattern matches
    for pattern in status_patterns:
        if pattern.matches(status_transactions, current_status):
            return (True, status_transactions)

    return (False, [])


def task_matches_project_patterns(task, project_patterns, resolved_phids_by_pattern):
    """
    Check if a task matches the project filter criteria.

    For patterns with AND logic (multiple projects in one pattern):
    - Task must belong to ALL projects in at least one combination

    For patterns with OR logic (separate patterns from comma):
    - Task must belong to ANY project from ANY pattern

    Parameters
    ----------
    task : dict
        Task data from maniphest.search
    project_patterns : list
        List of ProjectPattern objects
    resolved_phids_by_pattern : list
        Either a list of PHIDs (for OR logic) or list of tuples (for AND logic)
        For 'dyn127*,dynatron': [['PHID-1', 'PHID-2', 'PHID-3'], ['PHID-4']]
        For 'dyn127*+dynatron': [[('PHID-1', 'PHID-4'), ('PHID-2', 'PHID-4'), ...]]

    Returns
    -------
    bool
        True if task matches the filter criteria, False otherwise
    """
    if not project_patterns or not resolved_phids_by_pattern:
        return True  # No filtering needed

    # Get project PHIDs from the boards the task is on
    # Project information is stored in attachments.columns.boards, not in fields
    boards = task.get("attachments", {}).get("columns", {}).get("boards", {})
    task_project_phids = set(boards.keys()) if boards else set()

    if not task_project_phids:
        # Task has no projects, can't match
        log.debug(f"Task {task.get('id')} has no projects")
        return False

    # Check each pattern (separated by comma = OR logic between patterns)
    for pattern, pattern_data in zip(project_patterns, resolved_phids_by_pattern):
        # If pattern has multiple project names (AND logic with +):
        # pattern_data is a list of tuples (combinations)
        if len(pattern.project_names) > 1:
            # AND logic: check if ANY combination matches
            for combo in pattern_data:
                combo_set = set(combo)
                if combo_set.issubset(task_project_phids):
                    return True
        else:
            # Single project in pattern (OR logic from wildcard expansion):
            # pattern_data is a flat list of PHIDs
            pattern_phids_set = set(pattern_data)
            if pattern_phids_set & task_project_phids:
                return True

    return False


def task_matches_any_pattern(
    phab, task, task_phid, patterns, board_phids, priority_map_func, transactions=None
):
    """
    Check if a task matches any of the given transition patterns.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    task : dict
        Task data from maniphest.search
    task_phid : str
        Task PHID
    patterns : list
        List of TransitionPattern objects
    board_phids : list
        List of board PHIDs to check (typically the project being searched)
    priority_map_func : callable
        Function to get priority map (needed for fetch_all_transactions)
    transactions : dict, optional
        Pre-fetched transactions dict with keys 'columns', 'priority', 'status'.
        If None, will fetch column transactions using the old method.

    Returns
    -------
    tuple
        (matches: bool, all_transitions: list, matching_board_phids: set)
        all_transitions contains all transaction details for the task
        matching_board_phids contains PHIDs of boards that matched the pattern
    """
    if not patterns:
        return (True, [], set())  # No filtering needed

    # Use pre-fetched transactions if provided, otherwise fetch
    if transactions is not None and "columns" in transactions:
        column_transactions = transactions["columns"]
    else:
        column_transactions = fetch_task_transactions(phab, task_phid)

    if not column_transactions and not any(
        any(cond.get("type") == "in" for cond in p.conditions) for p in patterns
    ):
        # No transactions and no in-only patterns
        return (False, [], set())

    # Track which boards matched the pattern
    matching_board_phids = set()

    # Check each board the task is on
    for board_phid in board_phids:
        column_info = get_column_info(phab, board_phid)
        current_column = get_current_column(task, board_phid, column_info)

        # Filter transactions to this board
        board_transactions = [
            t
            for t in column_transactions
            if t.get("newValue")
            and len(t["newValue"]) > 0
            and t["newValue"][0] == board_phid
        ]

        # Check if any pattern matches
        for pattern in patterns:
            if pattern.matches(board_transactions, current_column, column_info):
                matching_board_phids.add(board_phid)
                break  # Board matched, no need to check other patterns for this board

    # Return match status, all transitions, and which boards matched
    if matching_board_phids:
        return (True, column_transactions, matching_board_phids)

    return (False, [], set())
