# -*- coding: utf-8 -*-
"""Batch operations for edit commands."""

import logging
import sys
from collections import defaultdict

from phabfive.constants import TASK_POLICY_FIELDS
from phabfive.edit.formatters import display_changes, generate_partition_suggestions
from phabfive.edit.validators import (
    get_board_names,
    get_task_boards,
    validate_board_column_context,
)
from phabfive.editor import confirm_apply, open_tty, prompt_each, render_changes
from phabfive.policy import resolve_policy_value

log = logging.getLogger(__name__)


def _open_review_stream(task_count, assume_yes, interactive, dry_run):
    """Decide whether to review each task, and on what terminal.

    Args:
        task_count (int): Number of tasks that passed validation
        assume_yes (bool): --yes was given, so never ask
        interactive (bool): --interactive was given, so always ask
        dry_run (bool): Previewing only, so there is nothing to confirm

    Returns:
        A terminal to prompt on, True to prompt on stdin, or None to not review.
    """
    if assume_yes or dry_run:
        return None

    if interactive:
        # Explicitly asked for review, so reach past a piped stdin to the
        # terminal. No terminal means no human, so do not prompt.
        return sys.stdin if sys.stdin.isatty() else open_tty()

    # By default a single task applies directly, and a pipe is not reviewed:
    # prompting there would hang an agent that cannot answer.
    if task_count < 2 or not sys.stdin.isatty():
        return None

    return sys.stdin


def _needs_text_confirmation(validated_tasks, title, description):
    """Whether any task's title or description would actually change."""
    if title is None and description is None:
        return False

    for task in validated_tasks:
        fields = task["task_data"]["fields"]
        if description is not None:
            if fields.get("description", {}).get("raw", "") != description:
                return True
        if title is not None and fields.get("name", "") != title:
            return True

    return False


def _needs_policy_confirmation(validated_tasks, maniphest, visible_to, editable_by):
    """Whether any task's view or edit policy would actually change.

    A policy change joins the text guard rather than applying unreviewed. It
    is the more consequential of the two and the harder to notice: a retitled
    task is still on the board it was on, while one whose view policy has
    narrowed has simply gone - for everybody the new policy leaves out, there
    is nothing left to notice. Both are also silent in the same way, in that
    the object still exists and still looks fine to whoever made the change.

    So a batch that would move a policy with no terminal to show the change on
    fails for want of `--yes`, exactly as a retitle does.

    Deciding it needs the instance, unlike the text guard: `#infra` has to
    become a PHID before it can be compared with the policy in place. That is
    one resolution for the batch, not one per task.
    """
    asked = [
        (TASK_POLICY_FIELDS[key], value, option)
        for key, value, option in (
            ("view", visible_to, "--visible-to"),
            ("edit", editable_by, "--editable-by"),
        )
        if value is not None
    ]

    for field, value, option in asked:
        resolved = resolve_policy_value(maniphest.phab, value, option=option)

        for task in validated_tasks:
            policy = task["task_data"]["fields"].get("policy") or {}
            if policy.get(field) != resolved:
                return True

    return False


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
    visible_to=None,
    editable_by=None,
    dry_run=False,
    force=False,
    interactive=False,
    output_format=None,
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
        visible_to (str): Who can see it, the --visible-to policy
        editable_by (str): Who can edit it, the --editable-by policy
        dry_run (bool): Show changes without applying
        force (bool): Skip confirmation prompts
        interactive (bool): Review every change, even for a single task
        output_format (str): The format the caller asked for. A
            machine-readable one answers with the record `maniphest show`
            gives for every task that was actually edited, and every line
            of prose below moves to stderr so the stream stays parseable.
            None, the default, is the human path and prints as it always has.

    Returns:
        int: Return code (0 for success, 1 for failure)
    """
    from phabfive.cli.output import is_machine_format
    from phabfive.display import display_tasks

    machine = is_machine_format(output_format)
    preview = sys.stderr if machine else sys.stdout

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

    # Phase 1.5: decide how (and whether) to review each task
    review_stream = _open_review_stream(
        len(validated_tasks), force, interactive, dry_run
    )

    if interactive and not dry_run and review_stream is None:
        # Asked to see every change, but there is no terminal to show them on.
        # Applying unreviewed would be the opposite of what was asked.
        sys.stderr.write(
            "Error: --interactive needs a terminal and none is available\n"
        )
        sys.stderr.write("No tasks were modified.\n")
        return 1

    if (
        review_stream is None
        and not dry_run
        and (
            _needs_text_confirmation(validated_tasks, title, description)
            or _needs_policy_confirmation(
                validated_tasks, maniphest, visible_to, editable_by
            )
        )
    ):
        # No terminal to show N diffs on, so make the caller say so explicitly.
        confirmed, return_code = confirm_apply(force)
        if not confirmed:
            sys.stderr.write("No tasks were modified.\n")
            return return_code

    # Phase 2: Process all validated tasks
    success_count = 0
    skipped_count = 0
    error_count = 0
    quit_early = False
    # The tasks a machine-readable format answers with the records of: the
    # ones now at the state that was asked for. A task that needed no
    # transaction is one of them - "already there" is an answer about the
    # object, not an absence of one - while a dry run, a skip and a failure
    # are not.
    settled_ids = []

    for task in validated_tasks:
        task_id = task["task_id"]
        monogram = f"T{task_id}"

        try:
            transactions, changes = maniphest.build_task_edit(
                task_id,
                task["task_data"],
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
                visible_to=visible_to,
                editable_by=editable_by,
            )
        except Exception as e:
            log.debug(f"Failed to prepare edit for {monogram}: {e}")
            sys.stderr.write(f"Error editing {monogram}: {e}\n")
            error_count += 1
            continue

        if not transactions:
            print(f"{monogram}: No changes (already at target state)", file=preview)
            success_count += 1
            settled_ids.append(task_id)
            continue

        if review_stream is not None:
            stream = None if review_stream is sys.stdin else review_stream
            render_changes(
                monogram,
                changes,
                header=f"Would apply to {monogram}:",
                file=preview,
            )
            answer = prompt_each(monogram, stream)

            if answer == "n":
                skipped_count += 1
                continue
            if answer == "q":
                quit_early = True
                break
            if answer == "a":
                review_stream = None

        if dry_run:
            render_changes(
                monogram,
                changes,
                header=f"[DRY RUN] Would apply to {monogram}:",
                file=preview,
            )
            success_count += 1
            continue

        try:
            maniphest.apply_task_edit(task_id, transactions)
            success_count += 1
            settled_ids.append(task_id)
            display_changes(
                monogram, {"task_id": task_id, "changes": changes}, file=preview
            )

        except Exception as e:
            log.debug(f"Failed to edit task {monogram}: {e}")
            sys.stderr.write(f"Error editing {monogram}: {e}\n")
            error_count += 1
            # Continue processing other tasks

    if review_stream is not None and review_stream is not sys.stdin:
        review_stream.close()

    _print_summary(
        success_count, skipped_count, len(validated_tasks), quit_early, file=preview
    )

    # One query for the batch, and the same records `maniphest show` gives.
    if machine and settled_ids:
        display_tasks(
            maniphest.task_show([int(task_id) for task_id in settled_ids]),
            output_format,
            maniphest,
        )

    # Skipping and quitting are choices, not failures.
    return 1 if error_count else 0


def _print_summary(applied, skipped, total, quit_early, file=None):
    """Report what happened, naming anything left untouched."""
    parts = [f"Edited {applied}/{total} tasks"]
    if skipped:
        parts.append(f"{skipped} skipped")
    if quit_early:
        remaining = total - applied - skipped
        parts.append(f"{remaining} left unchanged (quit)")

    print(f"\n{', '.join(parts)}", file=file or sys.stdout)
