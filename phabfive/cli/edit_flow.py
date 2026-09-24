# -*- coding: utf-8 -*-
"""What `phabfive edit` and `phabfive maniphest edit` do at the terminal.

phabfive.edit plans an edit as data and applies it; everything around that
is here: reading the IDs from arguments or from YAML on stdin, opening
$EDITOR for a description, reviewing each task, asking for --yes when there
is nobody to review with, dry-run previews, the summary, the records a
machine-readable format answers with, and the exit status.
"""

import logging
import sys

from phabfive.cli.editor import (
    confirm_apply,
    edit_text,
    open_tty,
    prompt_each,
    render_changes,
)
from phabfive.constants import is_machine_format
from phabfive.edit.formatters import generate_partition_suggestions
from phabfive.edit.plan import EditFailure, plan_task_edits
from phabfive.exceptions import PhabfiveValidationException
from phabfive.maniphest.validators import validate_assignment
from phabfive.policy import validate_policy_value
from phabfive.retry import Pacer
from phabfive.yaml_utils import group_objects_by_type, parse_yaml_from_stdin

log = logging.getLogger(__name__)

#: Why a `K` monogram is refused. Not pending work: Phorge exposes
#: `passphrase.query` and nothing else - there is no `passphrase.edit` to
#: call - so the sentence names the missing endpoint and where the change
#: can be made instead, rather than implying a flag, a token or a newer
#: server would help. `phabfive.spec.references.UNCREATABLE_OBJECT_KEYS`
#: says the same thing about a create spec's `passphrases:` section.
#:
#: Named for the monogram rather than the application on purpose. CodeQL's
#: py/clear-text-logging-sensitive-data reads an identifier holding
#: "passphrase" as one holding a password, so writing this constant to
#: stderr was reported as logging a credential in clear text - three high
#: severity alerts for a fixed string with nothing in it. The message is
#: unchanged; only the name is, so the scanner is not tripped on every
#: branch that touches this file.
K_MONOGRAM_REFUSAL = (
    "Error: Passphrases cannot be edited: Phorge exposes no passphrase.edit "
    "endpoint. Credentials must be edited in the web UI.\n"
)


def run_edit(
    edit,
    object_id=None,
    title=None,
    priority=None,
    status=None,
    tag=None,
    column=None,
    assign=None,
    unassign=False,
    description=None,
    subscribe=None,
    unsubscribe=None,
    attach=None,
    detach=None,
    comment=None,
    space=None,
    visible_to=None,
    editable_by=None,
    dry_run=False,
    force=False,
    interactive=False,
    output_format=None,
):
    """Edit one or more Phabricator objects, as `phabfive edit` does.

    The terminal half of an edit: where the IDs come from (arguments or YAML
    on stdin), $EDITOR for a description, review and confirmation, dry-run
    previews, what is printed and the exit status. What would change, and
    making the change, is `edit.plan()` and `edit.apply()`.

    Args:
        edit (Edit): The app the edit goes through
        object_id (str): Object ID(s) - single (e.g., "T123") or comma-separated (e.g., "T123,T124")
        title (str): New title for the object
        priority (str): Priority to set (or "raise"/"lower")
        status (str): Status to set
        tag (str): Board name for column context
        column (str): Column name (or "forward"/"backward")
        assign (str): Username to assign
        unassign (bool): Remove the assignee
        description (str): Description text, or "" to open $EDITOR
        subscribe (list): Users to add as subscribers
        unsubscribe (list): Users to remove from the subscribers
        attach (list): Commits to attach, by monogram, hash or PHID
        detach (list): Commits to detach, spelled as for attach
        comment (str): Comment to add
        space (str): Space to move the object to
        visible_to (str): Who can see it, the --visible-to policy
        editable_by (str): Who can edit it, the --editable-by policy
        dry_run (bool): Show changes without applying
        force (bool): Skip confirmation prompts
        interactive (bool): Review every change, even for a single task
        output_format (str): The format the caller asked for. A
            machine-readable one answers with the record `maniphest
            show` gives for each task that was edited, and puts every
            line of prose on stderr. None keeps the human output.

    Returns:
        int: Return code (0 for success, 1 for failure)
    """
    # If no edit options provided, default to editing description in $EDITOR
    has_any_option = any(
        [
            title,
            priority,
            status,
            column,
            assign,
            unassign,
            description is not None,
            subscribe,
            unsubscribe,
            attach,
            detach,
            comment,
            space,
            visible_to,
            editable_by,
        ]
    )
    edit_description_in_editor = not has_any_option

    try:
        # Refused before a task is even fetched, and once for the whole
        # batch rather than once per task, because the API cannot be relied
        # on to notice: it reads a policy value it does not recognise as a
        # policy nobody satisfies, and so answers a typo with a
        # self-lockout error.
        validate_policy_value(visible_to, option="--visible-to")
        validate_policy_value(editable_by, option="--editable-by")
        validate_assignment(assign, unassign)

        # Auto-detect piped input
        has_piped_input = not sys.stdin.isatty()

        if object_id:
            # Parse object IDs (supports comma-separated)
            parsed_ids = edit.parse_object_ids(object_id)

            if len(parsed_ids) == 1:
                # Single object mode
                object_type, oid = parsed_ids[0]

                if object_type == "task":
                    return _edit_task_single(
                        edit,
                        oid,
                        title=title,
                        priority=priority,
                        status=status,
                        tag=tag,
                        column=column,
                        assign=assign,
                        unassign=unassign,
                        description=description,
                        subscribe=subscribe,
                        unsubscribe=unsubscribe,
                        attach=attach,
                        detach=detach,
                        comment=comment,
                        space=space,
                        visible_to=visible_to,
                        editable_by=editable_by,
                        dry_run=dry_run,
                        force=force,
                        interactive=interactive,
                        edit_description_in_editor=edit_description_in_editor,
                        output_format=output_format,
                    )
                elif object_type == "passphrase":
                    sys.stderr.write(K_MONOGRAM_REFUSAL)
                    return 1
                elif object_type == "paste":
                    sys.stderr.write("Error: Paste editing not yet implemented\n")
                    return 1
            else:
                # Multiple objects - batch mode from CLI args
                object_type = parsed_ids[0][0]  # All same type (validated above)

                # Editor mode not supported for batch operations
                if edit_description_in_editor:
                    sys.stderr.write(
                        "Error: Editing description in $EDITOR only works for single task.\n"
                        "Specify an edit option (e.g., --priority, --status) for batch operations.\n"
                    )
                    return 1

                if object_type == "task":
                    # Convert to batch format
                    tasks = [
                        {"object_type": "task", "object_id": oid, "data": {}}
                        for _, oid in parsed_ids
                    ]
                    return edit_tasks_batch(
                        tasks,
                        edit.maniphest,
                        title=title,
                        priority=priority,
                        status=status,
                        tag=tag,
                        column=column,
                        assign=assign,
                        unassign=unassign,
                        description=description,
                        subscribe=subscribe,
                        unsubscribe=unsubscribe,
                        attach=attach,
                        detach=detach,
                        comment=comment,
                        space=space,
                        visible_to=visible_to,
                        editable_by=editable_by,
                        dry_run=dry_run,
                        force=force,
                        interactive=interactive,
                        output_format=output_format,
                    )
                elif object_type == "passphrase":
                    sys.stderr.write(K_MONOGRAM_REFUSAL)
                    return 1
                elif object_type == "paste":
                    sys.stderr.write("Error: Paste editing not yet implemented\n")
                    return 1

        elif has_piped_input:
            # Batch mode (auto-detected from pipe)

            # Editor mode not supported for batch operations
            if edit_description_in_editor:
                sys.stderr.write(
                    "Error: Editing description in $EDITOR only works for single task.\n"
                    "Specify an edit option (e.g., --priority, --status) for batch operations.\n"
                )
                return 1

            objects = parse_yaml_from_stdin(edit.parse_monogram)
            if not objects:
                sys.stderr.write("Error: No objects found in stdin\n")
                return 1

            # Group by object type
            grouped = group_objects_by_type(objects)

            # Process tasks
            if "task" in grouped:
                retcode = edit_tasks_batch(
                    grouped["task"],
                    edit.maniphest,
                    title=title,
                    priority=priority,
                    status=status,
                    tag=tag,
                    column=column,
                    assign=assign,
                    unassign=unassign,
                    description=description,
                    subscribe=subscribe,
                    unsubscribe=unsubscribe,
                    attach=attach,
                    detach=detach,
                    comment=comment,
                    space=space,
                    visible_to=visible_to,
                    editable_by=editable_by,
                    dry_run=dry_run,
                    force=force,
                    interactive=interactive,
                    output_format=output_format,
                )
                if retcode != 0:
                    return retcode

            # A passphrase cannot be edited at all; a paste not yet
            if "passphrase" in grouped:
                sys.stderr.write(K_MONOGRAM_REFUSAL)
                return 1
            if "paste" in grouped:
                sys.stderr.write("Error: Paste editing not yet implemented\n")
                return 1

            return 0

        else:
            # Error: no input provided
            sys.stderr.write(
                "Error: Object ID required (e.g., T123) or pipe YAML from stdin\n"
            )
            return 1

    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    except Exception as e:
        log.debug(f"Unexpected error during edit: {e}")
        sys.stderr.write(f"Error: {e}\n")
        return 1


def _edit_task_single(
    edit,
    task_id,
    title=None,
    priority=None,
    status=None,
    tag=None,
    column=None,
    assign=None,
    unassign=False,
    description=None,
    subscribe=None,
    unsubscribe=None,
    attach=None,
    detach=None,
    comment=None,
    space=None,
    visible_to=None,
    editable_by=None,
    dry_run=False,
    force=False,
    interactive=False,
    edit_description_in_editor=False,
    output_format=None,
):
    """Edit a single task.

    Args:
        task_id (str): Task ID (numeric, e.g., "123")
        title (str): New title for the task
        priority (str): Priority to set (or "raise"/"lower")
        status (str): Status to set
        tag (str): Board name for column context
        column (str): Column name (or "forward"/"backward")
        assign (str): Username to assign
        unassign (bool): Remove the assignee
        description (str): Description text, "" to clear, "-" to read from stdin
        subscribe (list): Users to add as subscribers
        unsubscribe (list): Users to remove from the subscribers
        attach (list): Commits to attach, by monogram, hash or PHID
        detach (list): Commits to detach, spelled as for attach
        comment (str): Comment to add
        space (str): Space to move the task to
        visible_to (str): Who can see it, the --visible-to policy
        editable_by (str): Who can edit it, the --editable-by policy
        dry_run (bool): Show changes without applying
        force (bool): Skip confirmation prompts
        interactive (bool): Review every change, even for a single task
        edit_description_in_editor (bool): Open $EDITOR for description
        output_format (str): The format the caller asked for; a
            machine-readable one answers with the task's own record

    Returns:
        int: Return code (0 for success, 1 for failure)
    """
    from phabfive.display import display_tasks

    machine = is_machine_format(output_format)
    preview = sys.stderr if machine else sys.stdout

    try:
        # Fetch current task state
        task_data = edit.maniphest._get_task_data(task_id)

        # Handle description
        final_description = None

        if edit_description_in_editor:
            current_desc = task_data["fields"].get("description", {}).get("raw", "")

            # Open editor with current description
            new_desc = edit_text(current_desc, prefix="description-")
            if new_desc is None:
                print("Description edit cancelled", file=preview)
                return 0

            final_description = new_desc
        elif description == "-":
            # Read from stdin
            if sys.stdin.isatty():
                sys.stderr.write("Error: --description - requires input from stdin\n")
                return 1
            new_desc = sys.stdin.read().rstrip()
            final_description = new_desc
        elif description is not None:
            # Use provided description (including empty string to clear)
            final_description = description

        if interactive:
            # --interactive gives one task the same review as a batch, so
            # there is a single review implementation to reason about.
            return edit_tasks_batch(
                [{"object_type": "task", "object_id": task_id, "data": {}}],
                edit.maniphest,
                title=title,
                priority=priority,
                status=status,
                tag=tag,
                column=column,
                assign=assign,
                unassign=unassign,
                description=final_description,
                subscribe=subscribe,
                unsubscribe=unsubscribe,
                attach=attach,
                detach=detach,
                comment=comment,
                space=space,
                visible_to=visible_to,
                editable_by=editable_by,
                dry_run=dry_run,
                interactive=True,
                output_format=output_format,
            )

        try:
            plan = plan_task_edits(
                edit.maniphest,
                [task_id],
                title=title,
                priority=priority,
                status=status,
                tag=tag,
                column=column,
                assign=assign,
                unassign=unassign,
                description=final_description,
                subscribe=subscribe,
                unsubscribe=unsubscribe,
                attach=attach,
                detach=detach,
                comment=comment,
                space=space,
                visible_to=visible_to,
                editable_by=editable_by,
                task_data={task_id: task_data},
            )
        except PhabfiveValidationException as e:
            [problem] = e.problems
            sys.stderr.write(f"Error: {problem.message}\n")

            # For multiple boards error, show copy-paste ready commands (up to 5 boards)
            if problem.boards and len(problem.boards) <= 5:
                sys.stderr.write("\nSuggested commands:\n\n")
                for board_name in sorted(problem.boards):
                    sys.stderr.write(f"# Move on {board_name}:\n")
                    sys.stderr.write(
                        f'phabfive edit T{task_id} --tag="{board_name}" --column={column}\n\n'
                    )

            return 1

        [entry] = plan.entries
        if isinstance(entry, EditFailure):
            raise entry.error

        if entry.noop:
            result = {"task_id": task_id, "changes": []}
        elif dry_run:
            render_changes(
                entry.monogram,
                entry.changes,
                header=f"[DRY RUN] Would apply to {entry.monogram}:",
                file=preview,
            )
            result = {"task_id": task_id, "changes": entry.changes, "dry_run": True}
        else:
            result = edit.apply(entry)

        # Display the changes
        display_changes(f"T{task_id}", result, file=preview)

        # A dry run wrote nothing, so there is no record to answer with,
        # and stdout stays empty rather than carrying the preview above.
        # A task that needed no transaction does have one: "already at
        # the target state" is an answer about the task, and a caller
        # parsing the stream should not have to special-case it as
        # silence.
        if machine and not result.get("dry_run"):
            display_tasks(
                edit.maniphest.task_show([int(task_id)]),
                output_format,
                edit.maniphest,
            )

        return 0

    except Exception as e:
        log.debug(f"Failed to edit task T{task_id}: {e}")
        sys.stderr.write(f"Error editing T{task_id}: {e}\n")
        return 1


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
    unassign=False,
    description=None,
    subscribe=None,
    unsubscribe=None,
    attach=None,
    detach=None,
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
        unassign (bool): Remove the assignee
        description (str): Description text to set
        subscribe (list): Users to add as subscribers
        unsubscribe (list): Users to remove from the subscribers
        attach (list): Commits to attach, by monogram, hash or PHID
        detach (list): Commits to detach, spelled as for attach
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
            unassign=unassign,
            description=description,
            subscribe=subscribe,
            unsubscribe=unsubscribe,
            attach=attach,
            detach=detach,
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
    unchanged_count = 0
    skipped_count = 0
    error_count = 0
    quit_early = False
    # The tasks a machine-readable format answers with the records of: the
    # ones now at the state that was asked for. A task that needed no
    # transaction is one of them - "already there" is an answer about the
    # object, not an absence of one - while a dry run, a skip and a failure
    # are not.
    settled_ids = []
    # PHAB_PACE seconds between writes, so a batch does not keep hammering a
    # server that is already struggling. Off unless configured.
    pacer = Pacer.from_conf(maniphest.conf)

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
            unchanged_count += 1
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
            pacer.wait()
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
        success_count,
        skipped_count,
        len(plan.entries),
        quit_early,
        file=preview,
        unchanged=unchanged_count,
        failed=error_count,
        dry_run=dry_run,
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


def _print_summary(
    applied,
    skipped,
    total,
    quit_early,
    file=None,
    unchanged=0,
    failed=0,
    dry_run=False,
):
    """Report what happened, naming anything left untouched.

    `applied` counts the tasks that changed - or would have, in a dry run,
    which says so rather than claiming an edit it did not make (#423). A task
    already at the target is counted apart, since nothing was done to it.
    """
    if dry_run:
        parts = [f"Would edit {applied}/{total} tasks (dry run)"]
    else:
        parts = [f"Edited {applied}/{total} tasks"]
    if unchanged:
        parts.append(f"{unchanged} already at target")
    if skipped:
        parts.append(f"{skipped} skipped")
    if quit_early:
        remaining = total - applied - unchanged - skipped - failed
        parts.append(f"{remaining} left unchanged (quit)")

    print(f"\n{', '.join(parts)}", file=file or sys.stdout)


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


__all__ = ["display_changes", "edit_tasks_batch", "run_edit"]
