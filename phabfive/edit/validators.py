# -*- coding: utf-8 -*-
"""Validators for edit operations."""


def get_task_boards(task_data):
    """Extract board PHIDs from task data.

    Args:
        task_data (dict): Task data from maniphest.search with attachments

    Returns:
        list: List of board PHIDs the task is on
    """
    try:
        boards = task_data.get("attachments", {}).get("columns", {}).get("boards", {})
        return list(boards.keys())
    except Exception:
        return []


def get_board_names(board_phids, phab):
    """Get display names for board PHIDs.

    Args:
        board_phids (list): List of board PHIDs
        phab: Phabricator API client

    Returns:
        list: List of board names
    """
    try:
        results = phab.project.search(constraints={"phids": board_phids})
        names = [proj["fields"]["name"] for proj in results["data"]]
        return names
    except Exception:
        # Fallback to PHIDs if we can't resolve names
        return board_phids


def validate_board_column_context(
    task_id, task_data, column_arg, board_phid, maniphest
):
    """Validate board/column context for a single task.

    Args:
        task_id (str): Task ID (e.g., "123")
        task_data (dict): Current task data from API (with attachments)
        column_arg (str): Value of --column flag (e.g., "Done", "forward", "backward")
        board_phid (list): The boards --tag named, already resolved, or None
        maniphest: Maniphest instance for naming the boards of an error

    Returns:
        tuple: (board_phids, error_message)
               board_phids is None if error, error_message is None if success

    A task has a position on every board it is on, so a column move names the
    boards it applies to: all of the ones --tag named, or - when it named
    none - the task's own, and only a task on exactly one board has an answer
    that is not a guess.
    """
    if column_arg is None:
        # No column change requested, no validation needed
        return (None, None)

    named = [board_phid] if isinstance(board_phid, str) else list(board_phid or [])

    # Get list of boards this task is on
    task_boards = get_task_boards(task_data)

    if named:
        # User specified the boards
        return (named, None)

    # No board specified, try to auto-detect
    if len(task_boards) == 0:
        return (
            None,
            f"Task T{task_id} is not on any boards. Use --tag=BOARD to specify which board to add it to.",
        )
    elif len(task_boards) == 1:
        # Single board, auto-detect
        return ([task_boards[0]], None)
    else:
        # Multiple boards, cannot auto-detect
        board_names = get_board_names(task_boards, maniphest.phab)
        return (
            None,
            f"Task T{task_id} is on multiple boards {board_names}. "
            "Use --tag=BOARD for each board to move the task on.",
        )
