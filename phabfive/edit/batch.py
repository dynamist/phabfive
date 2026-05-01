# -*- coding: utf-8 -*-
"""Batch operations for edit commands."""

import logging
import sys
from collections import defaultdict

from phabfive.edit.formatters import display_changes, generate_partition_suggestions
from phabfive.edit.validators import (
    get_board_names,
    get_task_boards,
    validate_board_column_context,
)

log = logging.getLogger(__name__)


def edit_tasks_batch(
    tasks,
    maniphest,
    priority=None,
    status=None,
    tag=None,
    column=None,
    assign=None,
    description=None,
    subscribe=None,
    comment=None,
    dry_run=False,
):
    """Edit multiple tasks in batch (atomic validation).

    Args:
        tasks (list): List of task dicts with 'object_id' key
        maniphest: Maniphest instance for editing tasks
        priority (str): Priority to set (or "raise"/"lower")
        status (str): Status to set
        tag (str): Board name for column context
        column (str): Column name (or "forward"/"backward")
        assign (str): Username to assign
        description (str): Description text to set
        subscribe (list): Usernames to add as subscribers
        comment (str): Comment to add
        dry_run (bool): Show changes without applying

    Returns:
        int: Return code (0 for success, 1 for failure)
    """
    # Phase 1: Validate ALL tasks before processing ANY (atomic batch)
    validation_errors = []
    errors_by_boards = defaultdict(list)
    validated_tasks = []

    for task in tasks:
        task_id = task["object_id"]

        try:
            # Fetch current task state
            task_data = maniphest._get_task_data(task_id)

            # Validate board/column context
            board_phid, error = validate_board_column_context(
                task_id, task_data, column, tag, maniphest
            )

            if error:
                validation_errors.append(f"T{task_id}: {error}")

                # Track for partition suggestions
                if "multiple boards" in error:
                    boards = get_task_boards(task_data)
                    board_names = get_board_names(boards, maniphest.phab)
                    errors_by_boards[frozenset(board_names)].append(task_id)

            else:
                validated_tasks.append(
                    {
                        "task_id": task_id,
                        "task_data": task_data,
                        "board_phid": board_phid,
                    }
                )

        except Exception as e:
            validation_errors.append(f"T{task_id}: {e}")

    # If any validation errors, fail atomically
    if validation_errors:
        sys.stderr.write(
            f"Error: Validation failed for {len(validation_errors)} task(s):\n"
        )
        for error in validation_errors:
            sys.stderr.write(f"  - {error}\n")

        # Generate partition suggestions if applicable
        if errors_by_boards:
            suggestions = generate_partition_suggestions(errors_by_boards)
            sys.stderr.write(suggestions)
            sys.stderr.write("\n")

        sys.stderr.write("\nNo tasks were modified (atomic batch failure).\n")
        return 1

    # Phase 2: Process all validated tasks
    success_count = 0
    for task in validated_tasks:
        try:
            result = maniphest.edit_task_by_id(
                task_id=task["task_id"],
                priority=priority,
                status=status,
                board_phid=task["board_phid"],
                column=column,
                assign=assign,
                description=description,
                subscribe=subscribe,
                comment=comment,
                dry_run=dry_run,
            )
            success_count += 1
            display_changes(f"T{task['task_id']}", result)

        except Exception as e:
            log.exception(f"Failed to edit task T{task['task_id']}")
            sys.stderr.write(f"Error editing T{task['task_id']}: {e}\n")
            # Continue processing other tasks

    print(f"\nEdited {success_count}/{len(validated_tasks)} tasks")
    return 0 if success_count == len(validated_tasks) else 1
