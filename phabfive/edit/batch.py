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
from phabfive.editor import confirm_apply, show_diff

log = logging.getLogger(__name__)


def _confirm_batch_text_changes(validated_tasks, title, description, force, dry_run):
    """Show one diff per task for a title/description rewrite, then confirm once.

    Args:
        validated_tasks (list): Dicts with 'task_id' and 'task_data' keys
        title (str): New title, or None
        description (str): New description, or None
        force (bool): Skip the confirmation prompt
        dry_run (bool): Previewing only, so there is nothing to confirm

    Returns:
        tuple: (confirmed: bool, return_code: int or None)
    """
    if dry_run or (title is None and description is None):
        return (True, None)

    changing = 0
    for task in validated_tasks:
        fields = task["task_data"]["fields"]
        task_id = task["task_id"]
        changed = False

        if description is not None:
            current = fields.get("description", {}).get("raw", "")
            if current != description:
                print()
                show_diff(current, description, filename=f"T{task_id}/description")
                changed = True

        if title is not None:
            current = fields.get("name", "")
            if current != title:
                print()
                show_diff(current, title, filename=f"T{task_id}/title")
                changed = True

        if changed:
            changing += 1

    if not changing:
        return (True, None)

    print()
    return confirm_apply(force, prompt=f"Apply changes to {changing} task(s)?")


def edit_tasks_batch(
    tasks,
    maniphest,
    title=None,
    priority=None,
    status=None,
    tag=None,
    column=None,
    assign=None,
    description=None,
    subscribe=None,
    comment=None,
    space=None,
    dry_run=False,
    force=False,
):
    """Edit multiple tasks in batch (atomic validation).

    Args:
        tasks (list): List of task dicts with 'object_id' key
        maniphest: Maniphest instance for editing tasks
        title (str): New title for the tasks
        priority (str): Priority to set (or "raise"/"lower")
        status (str): Status to set
        tag (str): Board name for column context
        column (str): Column name (or "forward"/"backward")
        assign (str): Username to assign
        description (str): Description text to set
        subscribe (list): Usernames to add as subscribers
        comment (str): Comment to add
        space (str): Space to move the tasks to
        dry_run (bool): Show changes without applying
        force (bool): Skip confirmation prompts

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

    # Phase 1.5: confirm title/description rewrites, as the single-task path does.
    # Column, status and priority edits stay promptless.
    confirmed, return_code = _confirm_batch_text_changes(
        validated_tasks, title, description, force, dry_run
    )
    if not confirmed:
        sys.stderr.write("No tasks were modified.\n")
        return return_code

    # Phase 2: Process all validated tasks
    success_count = 0
    for task in validated_tasks:
        try:
            result = maniphest.edit_task_by_id(
                task_id=task["task_id"],
                title=title,
                priority=priority,
                status=status,
                board_phid=task["board_phid"],
                column=column,
                assign=assign,
                description=description,
                subscribe=subscribe,
                comment=comment,
                space=space,
                dry_run=dry_run,
            )
            success_count += 1
            display_changes(f"T{task['task_id']}", result)

        except Exception as e:
            log.debug(f"Failed to edit task T{task['task_id']}: {e}")
            sys.stderr.write(f"Error editing T{task['task_id']}: {e}\n")
            # Continue processing other tasks

    print(f"\nEdited {success_count}/{len(validated_tasks)} tasks")
    return 0 if success_count == len(validated_tasks) else 1
