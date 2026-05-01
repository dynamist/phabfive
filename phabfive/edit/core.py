# -*- coding: utf-8 -*-
"""Edit module for phabfive.

Provides a unified edit command that auto-detects object type from monogram
and routes to the appropriate editor.
"""

import logging
import sys

from phabfive.core import Phabfive
from phabfive.edit.batch import edit_tasks_batch
from phabfive.edit.formatters import display_changes
from phabfive.edit.validators import (
    get_board_names,
    get_task_boards,
    validate_board_column_context,
)
from phabfive.editor import confirm_text_change
from phabfive.maniphest import Maniphest
from phabfive.yaml_utils import group_objects_by_type, parse_yaml_from_stdin

log = logging.getLogger(__name__)


class Edit(Phabfive):
    """Edit handler for Phabricator objects."""

    def __init__(self):
        """Initialize the Edit handler."""
        super().__init__()
        self.maniphest = Maniphest()

    def edit_objects(
        self,
        object_id=None,
        priority=None,
        status=None,
        tag=None,
        column=None,
        assign=None,
        description=None,
        subscribe=None,
        comment=None,
        dry_run=False,
        force=False,
    ):
        """Edit one or more Phabricator objects.

        Args:
            object_id (str): Object ID(s) - single (e.g., "T123") or comma-separated (e.g., "T123,T124")
            priority (str): Priority to set (or "raise"/"lower")
            status (str): Status to set
            tag (str): Board name for column context
            column (str): Column name (or "forward"/"backward")
            assign (str): Username to assign
            description (str): Description text, or "" to open $EDITOR
            subscribe (list): Usernames to add as subscribers
            comment (str): Comment to add
            dry_run (bool): Show changes without applying
            force (bool): Skip confirmation prompts

        Returns:
            int: Return code (0 for success, 1 for failure)
        """
        # If no edit options provided, default to editing description in $EDITOR
        has_any_option = any(
            [
                priority,
                status,
                column,
                assign,
                description is not None,
                subscribe,
                comment,
            ]
        )
        edit_description_in_editor = not has_any_option

        try:
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
                            priority=priority,
                            status=status,
                            tag=tag,
                            column=column,
                            assign=assign,
                            description=description,
                            subscribe=subscribe,
                            comment=comment,
                            dry_run=dry_run,
                            force=force,
                            edit_description_in_editor=edit_description_in_editor,
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
                            priority=priority,
                            status=status,
                            tag=tag,
                            column=column,
                            assign=assign,
                            description=description,
                            subscribe=subscribe,
                            comment=comment,
                            dry_run=dry_run,
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
                        priority=priority,
                        status=status,
                        tag=tag,
                        column=column,
                        assign=assign,
                        description=description,
                        subscribe=subscribe,
                        comment=comment,
                        dry_run=dry_run,
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
        priority=None,
        status=None,
        tag=None,
        column=None,
        assign=None,
        description=None,
        subscribe=None,
        comment=None,
        dry_run=False,
        force=False,
        edit_description_in_editor=False,
    ):
        """Edit a single task.

        Args:
            task_id (str): Task ID (numeric, e.g., "123")
            priority (str): Priority to set (or "raise"/"lower")
            status (str): Status to set
            tag (str): Board name for column context
            column (str): Column name (or "forward"/"backward")
            assign (str): Username to assign
            description (str): Description text, "" to clear, "-" to read from stdin
            subscribe (list): Usernames to add as subscribers
            comment (str): Comment to add
            dry_run (bool): Show changes without applying
            force (bool): Skip confirmation prompts
            edit_description_in_editor (bool): Open $EDITOR for description

        Returns:
            int: Return code (0 for success, 1 for failure)
        """
        try:
            # Fetch current task state
            task_data = self.maniphest._get_task_data(task_id)

            # Handle description
            final_description = None
            current_desc = task_data["fields"].get("description", {}).get("raw", "")

            if edit_description_in_editor:
                # Open editor with current description
                from phabfive.editor import edit_text

                new_desc = edit_text(current_desc)
                if new_desc is None:
                    print("Description edit cancelled")
                    return 0

                confirmed, return_code = confirm_text_change(
                    current_desc, new_desc, force
                )
                if not confirmed:
                    return return_code

                final_description = new_desc
            elif description == "-":
                # Read from stdin
                if sys.stdin.isatty():
                    sys.stderr.write(
                        "Error: --description - requires input from stdin\n"
                    )
                    return 1
                new_desc = sys.stdin.read().rstrip()

                confirmed, return_code = confirm_text_change(
                    current_desc, new_desc, force
                )
                if not confirmed:
                    return return_code

                final_description = new_desc
            elif description is not None:
                # Use provided description (including empty string to clear)
                confirmed, return_code = confirm_text_change(
                    current_desc, description, force
                )
                if not confirmed:
                    return return_code

                final_description = description

            # Validate board/column context
            board_phid, error = validate_board_column_context(
                task_id, task_data, column, tag, self.maniphest
            )
            if error:
                sys.stderr.write(f"Error: {error}\n")

                # For multiple boards error, show copy-paste ready commands (up to 5 boards)
                if "multiple boards" in error and column:
                    boards = get_task_boards(task_data)
                    board_names = get_board_names(boards, self.phab)
                    if len(board_names) <= 5:
                        sys.stderr.write("\nSuggested commands:\n\n")
                        for board_name in sorted(board_names):
                            sys.stderr.write(f"# Move on {board_name}:\n")
                            sys.stderr.write(
                                f'phabfive edit T{task_id} --tag="{board_name}" --column={column}\n\n'
                            )

                return 1

            # Delegate to maniphest module
            result = self.maniphest.edit_task_by_id(
                task_id=task_id,
                priority=priority,
                status=status,
                board_phid=board_phid,
                column=column,
                assign=assign,
                description=final_description,
                subscribe=subscribe,
                comment=comment,
                dry_run=dry_run,
            )

            # Display the changes
            display_changes(f"T{task_id}", result)
            return 0

        except Exception as e:
            log.debug(f"Failed to edit task T{task_id}: {e}")
            sys.stderr.write(f"Error editing T{task_id}: {e}\n")
            return 1
