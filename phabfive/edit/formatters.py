# -*- coding: utf-8 -*-
"""Formatters for edit operations."""

import sys


def display_changes(task_monogram, result, file=None):
    """Display the changes made to a task.

    Args:
        task_monogram (str): Task monogram (e.g., "T123")
        result (dict): Result from edit_task_by_id with 'changes' list
        file: Stream to write to; defaults to stdout. Under a
            machine-readable format the caller passes stderr, and the task's
            record is what goes to stdout instead.
    """
    stream = file or sys.stdout

    if not result:
        print(f"{task_monogram}: No changes", file=stream)
        return

    changes = result.get("changes", [])
    is_dry_run = result.get("dry_run", False)

    if not changes:
        print(f"{task_monogram}: No changes (already at target state)", file=stream)
        return

    if is_dry_run:
        # Dry run output is already printed by edit_task_by_id
        return

    print(f"{task_monogram}:", file=stream)
    for change in changes:
        field = change["field"]
        old_val = change["old"]
        new_val = change["new"]

        if old_val is None:
            # For comment, just show "Added"
            print(f"  {field}: {new_val}", file=stream)
        else:
            print(f"  {field}: {old_val} → {new_val}", file=stream)


def generate_partition_suggestions(errors_by_boards):
    """Generate suggested commands to partition tasks by board membership.

    Args:
        errors_by_boards (dict): Dict mapping frozenset of board names -> list of task IDs

    Returns:
        str: Multi-line string with suggested partition commands
    """
    suggestions = []
    suggestions.append("\nSuggested partition commands:")
    suggestions.append("")

    for boards, task_ids in errors_by_boards.items():
        board_list = sorted(boards)
        board_str = " + ".join(board_list)
        task_str = ",".join([f"T{tid}" for tid in task_ids])

        suggestions.append(f"# Tasks on {board_str}:")
        # Pick the first board as the target (user can adjust)
        target_board = board_list[0]
        suggestions.append(
            f'phabfive edit {task_str} --tag="{target_board}" --column=COLUMN'
        )
        suggestions.append("")

    return "\n".join(suggestions)
