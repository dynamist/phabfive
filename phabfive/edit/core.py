# -*- coding: utf-8 -*-
"""Edit module for phabfive.

Provides a unified edit command that auto-detects object type from monogram
and routes to the appropriate editor.
"""

import functools
import logging
import sys

from phabfive.core import Phabfive
from phabfive.edit.batch import edit_tasks_batch
from phabfive.edit.formatters import display_changes
from phabfive.edit.plan import EditFailure, plan_task_edits
from phabfive.editor import render_changes
from phabfive.exceptions import PhabfiveInputException, PhabfiveValidationException
from phabfive.maniphest import Maniphest
from phabfive.policy import validate_policy_value
from phabfive.yaml_utils import group_objects_by_type, parse_yaml_from_stdin

log = logging.getLogger(__name__)


class Edit(Phabfive):
    """Edit handler for Phabricator objects."""

    @functools.cached_property
    def maniphest(self):
        """A Maniphest sharing this instance's configuration and client."""
        return Maniphest._from_parent(self)

    def plan(
        self,
        object_ids,
        *,
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
    ):
        """Work out what an edit would change on each task, and change nothing.

        Parameters
        ----------
        object_ids : str or iterable
            Tasks, as "T1,T2", or an iterable of monograms ("T1") and
            numeric IDs (1 or "1").

        The changes are keyword arguments, as `maniphest edit` takes them:
        `priority` also takes "raise"/"lower", `column` also takes
        "forward"/"backward", and `tag` names the board `column` is on.

        Returns
        -------
        EditPlan
            A TaskEdit or an EditFailure per task, in the order given.

        Raises
        ------
        PhabfiveInputException
            When an ID is not a task's.
        PhabfiveValidationException
            When any task cannot be fetched, or its board is ambiguous. No
            edit is planned for any of them.
        """
        return plan_task_edits(
            self.maniphest,
            self._task_ids(object_ids),
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

    def apply(self, task_edit):
        """Make one planned edit.

        A no-op sends nothing. Returns {"task_id", "changes"}.

        Raises
        ------
        PhabfiveDataException
            When the server refuses the edit.
        """
        if not task_edit.noop:
            self.maniphest.apply_task_edit(task_edit.task_id, task_edit.transactions)
        return {"task_id": task_edit.task_id, "changes": task_edit.changes}

    def apply_all(self, plan):
        """Make every planned edit, in order, stopping at the first refused.

        A task in `plan.failures` was never planned and is not attempted.
        To carry on past a refusal, call `apply` for each edit instead.
        """
        return [self.apply(task_edit) for task_edit in plan.edits]

    def _task_ids(self, object_ids):
        """Numeric task IDs from "T1,T2", or from monograms and numbers."""
        if isinstance(object_ids, str):
            parsed = self.parse_object_ids(object_ids)
        else:
            parsed = [
                ("task", str(oid))
                if isinstance(oid, int) or str(oid).isdigit()
                else self.parse_monogram(str(oid))
                for oid in object_ids
            ]

        for object_type, oid in parsed:
            if object_type != "task":
                raise PhabfiveInputException(
                    f"Only tasks can be edited, not a {object_type}: {oid}"
                )
        return [oid for _type, oid in parsed]

    def edit_objects(
        self,
        object_id=None,
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
        """Edit one or more Phabricator objects.

        Args:
            object_id (str): Object ID(s) - single (e.g., "T123") or comma-separated (e.g., "T123,T124")
            title (str): New title for the object
            priority (str): Priority to set (or "raise"/"lower")
            status (str): Status to set
            tag (str): Board name for column context
            column (str): Column name (or "forward"/"backward")
            assign (str): Username to assign
            description (str): Description text, or "" to open $EDITOR
            subscribe (list): Usernames to add as subscribers
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
                description is not None,
                subscribe,
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

            # Auto-detect piped input
            has_piped_input = not sys.stdin.isatty()

            if object_id:
                # Parse object IDs (supports comma-separated)
                parsed_ids = self.parse_object_ids(object_id)

                if len(parsed_ids) == 1:
                    # Single object mode
                    object_type, oid = parsed_ids[0]

                    if object_type == "task":
                        return self._edit_task_single(
                            oid,
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
                            dry_run=dry_run,
                            force=force,
                            interactive=interactive,
                            edit_description_in_editor=edit_description_in_editor,
                            output_format=output_format,
                        )
                    elif object_type == "passphrase":
                        sys.stderr.write(
                            "Error: Passphrase editing not yet implemented\n"
                        )
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
                            self.maniphest,
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
                            dry_run=dry_run,
                            force=force,
                            interactive=interactive,
                            output_format=output_format,
                        )
                    elif object_type == "passphrase":
                        sys.stderr.write(
                            "Error: Passphrase editing not yet implemented\n"
                        )
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

                objects = parse_yaml_from_stdin(self.parse_monogram)
                if not objects:
                    sys.stderr.write("Error: No objects found in stdin\n")
                    return 1

                # Group by object type
                grouped = group_objects_by_type(objects)

                # Process tasks
                if "task" in grouped:
                    retcode = edit_tasks_batch(
                        grouped["task"],
                        self.maniphest,
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
                        dry_run=dry_run,
                        force=force,
                        interactive=interactive,
                        output_format=output_format,
                    )
                    if retcode != 0:
                        return retcode

                # Passphrases and pastes not yet implemented
                if "passphrase" in grouped:
                    sys.stderr.write("Error: Passphrase editing not yet implemented\n")
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
        self,
        task_id,
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
            description (str): Description text, "" to clear, "-" to read from stdin
            subscribe (list): Usernames to add as subscribers
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
        from phabfive.constants import is_machine_format
        from phabfive.display import display_tasks

        machine = is_machine_format(output_format)
        preview = sys.stderr if machine else sys.stdout

        try:
            # Fetch current task state
            task_data = self.maniphest._get_task_data(task_id)

            # Handle description
            final_description = None

            if edit_description_in_editor:
                current_desc = task_data["fields"].get("description", {}).get("raw", "")

                # Open editor with current description
                from phabfive.editor import edit_text

                new_desc = edit_text(current_desc, prefix="description-")
                if new_desc is None:
                    print("Description edit cancelled", file=preview)
                    return 0

                final_description = new_desc
            elif description == "-":
                # Read from stdin
                if sys.stdin.isatty():
                    sys.stderr.write(
                        "Error: --description - requires input from stdin\n"
                    )
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
                    self.maniphest,
                    title=title,
                    priority=priority,
                    status=status,
                    tag=tag,
                    column=column,
                    assign=assign,
                    description=final_description,
                    subscribe=subscribe,
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
                    self.maniphest,
                    [task_id],
                    title=title,
                    priority=priority,
                    status=status,
                    tag=tag,
                    column=column,
                    assign=assign,
                    description=final_description,
                    subscribe=subscribe,
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
                result = self.apply(entry)

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
                    self.maniphest.task_show([int(task_id)]),
                    output_format,
                    self.maniphest,
                )

            return 0

        except Exception as e:
            log.debug(f"Failed to edit task T{task_id}: {e}")
            sys.stderr.write(f"Error editing T{task_id}: {e}\n")
            return 1


__all__ = ["Edit"]
