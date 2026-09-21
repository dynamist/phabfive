# -*- coding: utf-8 -*-
"""Batch operations for edit commands."""

import logging
import sys

from phabfive.edit.formatters import display_changes, generate_partition_suggestions
from phabfive.edit.plan import (  # noqa: F401 - the two confirmations, re-exported
    EditFailure,
    _needs_policy_confirmation,
    _needs_text_confirmation,
    plan_task_edits,
)
from phabfive.editor import confirm_apply, open_tty, prompt_each, render_changes
from phabfive.exceptions import PhabfiveValidationException

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

    Plans every edit with plan_task_edits, then reviews, confirms, previews
    and applies them one at a time.

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
    from phabfive.constants import is_machine_format
    from phabfive.display import display_tasks

    machine = is_machine_format(output_format)
    preview = sys.stderr if machine else sys.stdout

    # Phase 1: validate ALL tasks and plan every edit before applying ANY
    try:
        plan = plan_task_edits(
            maniphest,
            [task["object_id"] for task in tasks],
            title=title,
            priority=priority,
            status=status,
            tag=tag,
            column=column,
            assign=assign,
            description=description,
            subscribe=subscribe,
            comment=comment,
            space=space,
            visible_to=visible_to,
            editable_by=editable_by,
        )
    except PhabfiveValidationException as e:
        report_validation_failure(e)
        return 1

    # Phase 1.5: decide how (and whether) to review each task
    review_stream = _open_review_stream(len(plan.entries), force, interactive, dry_run)

    if interactive and not dry_run and review_stream is None:
        # Asked to see every change, but there is no terminal to show them on.
        # Applying unreviewed would be the opposite of what was asked.
        sys.stderr.write(
            "Error: --interactive needs a terminal and none is available\n"
        )
        sys.stderr.write("No tasks were modified.\n")
        return 1

    if review_stream is None and not dry_run and plan.needs_confirmation:
        # No terminal to show N diffs on, so make the caller say so explicitly.
        confirmed, return_code = confirm_apply(force)
        if not confirmed:
            sys.stderr.write("No tasks were modified.\n")
            return return_code

    # Phase 2: Process every planned task
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

    for entry in plan.entries:
        task_id = entry.task_id
        monogram = entry.monogram

        if isinstance(entry, EditFailure):
            log.debug(f"Failed to prepare edit for {monogram}: {entry.error}")
            sys.stderr.write(f"Error editing {monogram}: {entry.error}\n")
            error_count += 1
            continue

        if entry.noop:
            print(f"{monogram}: No changes (already at target state)", file=preview)
            success_count += 1
            settled_ids.append(task_id)
            continue

        if review_stream is not None:
            stream = None if review_stream is sys.stdin else review_stream
            render_changes(
                monogram,
                entry.changes,
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
                entry.changes,
                header=f"[DRY RUN] Would apply to {monogram}:",
                file=preview,
            )
            success_count += 1
            continue

        try:
            maniphest.apply_task_edit(task_id, entry.transactions)
            success_count += 1
            settled_ids.append(task_id)
            display_changes(
                monogram, {"task_id": task_id, "changes": entry.changes}, file=preview
            )

        except Exception as e:
            log.debug(f"Failed to edit task {monogram}: {e}")
            sys.stderr.write(f"Error editing {monogram}: {e}\n")
            error_count += 1
            # Continue processing other tasks

    if review_stream is not None and review_stream is not sys.stdin:
        review_stream.close()

    _print_summary(
        success_count, skipped_count, len(plan.entries), quit_early, file=preview
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


def report_validation_failure(error):
    """Say which tasks failed validation, and how to split the edit up."""
    sys.stderr.write(f"Error: Validation failed for {len(error.problems)} task(s):\n")
    for problem in error.problems:
        sys.stderr.write(f"  - {problem.monogram}: {problem.message}\n")

    # Generate partition suggestions if applicable
    if error.errors_by_boards:
        sys.stderr.write(generate_partition_suggestions(error.errors_by_boards))
        sys.stderr.write("\n")

    sys.stderr.write("\nNo tasks were modified (atomic batch failure).\n")


def _print_summary(applied, skipped, total, quit_early, file=None):
    """Report what happened, naming anything left untouched."""
    parts = [f"Edited {applied}/{total} tasks"]
    if skipped:
        parts.append(f"{skipped} skipped")
    if quit_early:
        remaining = total - applied - skipped
        parts.append(f"{remaining} left unchanged (quit)")

    print(f"\n{', '.join(parts)}", file=file or sys.stdout)
