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
        boards = (
            task_data.get("attachments", {}).get("columns", {}).get("boards", {})
        )
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


def validate_board_column_context(task_id, task_data, column_arg, tag_arg, maniphest):
    """Validate board/column context for a single task.

    Args:
        task_id (str): Task ID (e.g., "123")
        task_data (dict): Current task data from API (with attachments)
        column_arg (str): Value of --column flag (e.g., "Done", "forward", "backward")
        tag_arg (str): Value of --tag flag (board name) or None
        maniphest: Maniphest instance for resolving project PHIDs

    Returns:
        tuple: (board_phid, error_message)
               board_phid is None if error, error_message is None if success

    """
    if column_arg is None:
        # No column change requested, no validation needed
        return (None, None)

    # Get list of boards this task is on
    task_boards = get_task_boards(task_data)

    if tag_arg:
        # User specified a board, resolve it
        board_phids = maniphest._resolve_project_phids(tag_arg)
        if not board_phids:
            return (None, f"Board not found: {tag_arg}")
        board_phid = board_phids[0]
        return (board_phid, None)

    # No board specified, try to auto-detect
    if len(task_boards) == 0:
        return (
            None,
            f"Task T{task_id} is not on any boards. Use --tag=BOARD to specify which board to add it to.",
        )
    elif len(task_boards) == 1:
        # Single board, auto-detect
        return (task_boards[0], None)
    else:
        # Multiple boards, cannot auto-detect
        board_names = get_board_names(task_boards, maniphest.phab)
        return (
            None,
            f"Task T{task_id} is on multiple boards {board_names}. Use --tag=BOARD to specify which board.",
        )
