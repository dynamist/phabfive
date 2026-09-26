# -*- coding: utf-8 -*-
"""Formatters for edit operations."""


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
        # Every board they share, because a column move reaches each board it
        # names and one at a time is several edits for one change.
        suggestions.append(
            f'phabfive edit {task_str} --tag="{",".join(board_list)}" --column=COLUMN'
        )
        suggestions.append("")

    return "\n".join(suggestions)
