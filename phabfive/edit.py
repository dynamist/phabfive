# -*- coding: utf-8 -*-

"""
Edit module for phabfive.

Provides a unified edit command that auto-detects object type from monogram
and routes to the appropriate editor.
"""

import logging
import re
import sys
from collections import defaultdict

from ruamel.yaml import YAML

from phabfive.core import Phabfive
from phabfive.maniphest import Maniphest

log = logging.getLogger(__name__)


class Edit(Phabfive):
    """Edit handler for Phabricator objects."""

    # Monogram patterns
    TASK_PATTERN = re.compile(r"[Tt](\d+)")
    PASSPHRASE_PATTERN = re.compile(r"[Kk](\d+)")
    PASTE_PATTERN = re.compile(r"[Pp](\d+)")

    def __init__(self):
        """Initialize the Edit handler."""
        super().__init__()
        self.maniphest = Maniphest()

    def _parse_monogram(self, text):
        """Parse a monogram from text and return object type and ID.

        Args:
            text (str): Text containing a monogram (e.g., "T123", "https://phorge.example.com/T456")

        Returns:
            tuple: (object_type, object_id) where object_type is "task"|"passphrase"|"paste"|None
                   and object_id is the numeric ID

        Raises:
            ValueError: If no valid monogram is found
        """
        # Try task monogram
        match = self.TASK_PATTERN.search(text)
        if match:
            return ("task", match.group(1))

        # Try passphrase monogram
        match = self.PASSPHRASE_PATTERN.search(text)
        if match:
            return ("passphrase", match.group(1))

        # Try paste monogram
        match = self.PASTE_PATTERN.search(text)
        if match:
            return ("paste", match.group(1))

        raise ValueError(f"No valid monogram found in: {text}")

    def _parse_yaml_from_stdin(self):
        """Parse YAML documents from stdin.

        Returns:
            list: List of dicts, each containing:
                  - object_type: str ("task"|"passphrase"|"paste")
                  - object_id: str (numeric ID)
                  - data: dict (parsed YAML data)

        Raises:
            ValueError: If YAML is invalid or missing required fields
        """
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        objects = []

        # Read all YAML documents from stdin
        try:
            for doc in yaml.load_all(sys.stdin):
                if doc is None:
                    continue

                # Extract monogram from Link field
                if "Link" not in doc:
                    raise ValueError("YAML document missing 'Link' field")

                link = doc["Link"]
                object_type, object_id = self._parse_monogram(link)

                objects.append(
                    {"object_type": object_type, "object_id": object_id, "data": doc}
                )

        except Exception as e:
            raise ValueError(f"Failed to parse YAML from stdin: {e}")

        return objects

    def _group_objects_by_type(self, objects):
        """Group objects by their type.

        Args:
            objects (list): List of object dicts from _parse_yaml_from_stdin

        Returns:
            dict: Dict mapping object_type -> list of objects
        """
        grouped = defaultdict(list)
        for obj in objects:
            grouped[obj["object_type"]].append(obj)
        return dict(grouped)

    def _validate_board_column_context(self, task_id, task_data, column_arg, tag_arg):
        """Validate board/column context for a single task.

        Args:
            task_id (str): Task ID (e.g., "123")
            task_data (dict): Current task data from API (with attachments)
            column_arg (str): Value of --column flag (e.g., "Done", "forward", "backward")
            tag_arg (str): Value of --tag flag (board name) or None

        Returns:
            tuple: (board_phid, error_message)
                   board_phid is None if error, error_message is None if success

        """
        if column_arg is None:
            # No column change requested, no validation needed
            return (None, None)

        # Get list of boards this task is on
        task_boards = self._get_task_boards(task_data)

        if tag_arg:
            # User specified a board, resolve it
            board_phids = self.maniphest._resolve_project_phids(tag_arg)
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
            board_names = self._get_board_names(task_boards)
            return (
                None,
                f"Task T{task_id} is on multiple boards {board_names}. Use --tag=BOARD to specify which board.",
            )

    def _get_task_boards(self, task_data):
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

    def _get_board_names(self, board_phids):
        """Get display names for board PHIDs.

        Args:
            board_phids (list): List of board PHIDs

        Returns:
            list: List of board names
        """
        try:
            results = self.phab.project.search(constraints={"phids": board_phids})
            names = [proj["fields"]["name"] for proj in results["data"]]
            return names
        except Exception:
            # Fallback to PHIDs if we can't resolve names
            return board_phids

    def _generate_partition_suggestions(self, errors_by_boards):
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

    def _display_changes(self, task_monogram, result):
        """Display the changes made to a task.

        Args:
            task_monogram (str): Task monogram (e.g., "T123")
            result (dict): Result from edit_task_by_id with 'changes' list
        """
        if not result:
            print(f"{task_monogram}: No changes")
            return

        changes = result.get("changes", [])
        is_dry_run = result.get("dry_run", False)

        if not changes:
            print(f"{task_monogram}: No changes (already at target state)")
            return

        if is_dry_run:
            # Dry run output is already printed by edit_task_by_id
            return

        print(f"{task_monogram}:")
        for change in changes:
            field = change["field"]
            old_val = change["old"]
            new_val = change["new"]

            if old_val is None:
                # For comment, just show "Added"
                print(f"  {field}: {new_val}")
            else:
                print(f"  {field}: {old_val} → {new_val}")

    def _parse_object_ids(self, object_id_str):
        """Parse object ID string which may contain comma-separated IDs.

        Args:
            object_id_str (str): Single ID or comma-separated IDs (e.g., "T123" or "T123,T124,T125")

        Returns:
            list: List of (object_type, object_id) tuples

        Raises:
            ValueError: If any ID is invalid or types are mixed
        """
        # Split by comma and strip whitespace
        ids = [s.strip() for s in object_id_str.split(",") if s.strip()]

        if not ids:
            raise ValueError("No valid object IDs provided")

        results = []
        seen_types = set()

        for id_str in ids:
            obj_type, obj_id = self._parse_monogram(id_str)
            results.append((obj_type, obj_id))
            seen_types.add(obj_type)

        # Check for mixed types
        if len(seen_types) > 1:
            raise ValueError(
                f"Cannot mix object types in a single edit command: {seen_types}"
            )

        return results

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

        Returns:
            int: Return code (0 for success, 1 for failure)
        """
        # If no edit options provided, default to editing description in $EDITOR
        has_any_option = any([
            priority, status, column, assign, description is not None, subscribe, comment
        ])
        edit_description_in_editor = not has_any_option

        try:
            # Auto-detect piped input
            has_piped_input = not sys.stdin.isatty()

            if object_id:
                # Parse object IDs (supports comma-separated)
                parsed_ids = self._parse_object_ids(object_id)

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
                        return self._edit_tasks_batch(
                            tasks,
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

                objects = self._parse_yaml_from_stdin()
                if not objects:
                    sys.stderr.write("Error: No objects found in stdin\n")
                    return 1

                # Group by object type
                grouped = self._group_objects_by_type(objects)

                # Process tasks
                if "task" in grouped:
                    retcode = self._edit_tasks_batch(
                        grouped["task"],
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
            log.exception("Unexpected error during edit")
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
            edit_description_in_editor (bool): Open $EDITOR for description

        Returns:
            int: Return code (0 for success, 1 for failure)
        """
        try:
            # Fetch current task state
            task_data = self.maniphest._get_task_data(task_id)

            # Handle description
            final_description = None
            if edit_description_in_editor:
                # Open editor with current description
                from phabfive.editor import edit_text

                current_desc = task_data["fields"].get("description", {}).get("raw", "")
                final_description = edit_text(current_desc)
                if final_description is None:
                    print("Description edit cancelled")
                    return 0
            elif description == "-":
                # Read from stdin
                if sys.stdin.isatty():
                    sys.stderr.write("Error: --description - requires input from stdin\n")
                    return 1
                final_description = sys.stdin.read().rstrip()
            elif description is not None:
                # Use provided description (including empty string to clear)
                final_description = description

            # Validate board/column context
            board_phid, error = self._validate_board_column_context(
                task_id, task_data, column, tag
            )
            if error:
                sys.stderr.write(f"Error: {error}\n")

                # For multiple boards error, show copy-paste ready commands (up to 5 boards)
                if "multiple boards" in error and column:
                    boards = self._get_task_boards(task_data)
                    board_names = self._get_board_names(boards)
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
            self._display_changes(f"T{task_id}", result)
            return 0

        except Exception as e:
            log.exception(f"Failed to edit task T{task_id}")
            sys.stderr.write(f"Error editing T{task_id}: {e}\n")
            return 1

    def _edit_tasks_batch(
        self,
        tasks,
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
            tasks (list): List of task dicts from _parse_yaml_from_stdin
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
                task_data = self.maniphest._get_task_data(task_id)

                # Validate board/column context
                board_phid, error = self._validate_board_column_context(
                    task_id, task_data, column, tag
                )

                if error:
                    validation_errors.append(f"T{task_id}: {error}")

                    # Track for partition suggestions
                    if "multiple boards" in error:
                        boards = self._get_task_boards(task_data)
                        board_names = self._get_board_names(boards)
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
                suggestions = self._generate_partition_suggestions(errors_by_boards)
                sys.stderr.write(suggestions)
                sys.stderr.write("\n")

            sys.stderr.write("\nNo tasks were modified (atomic batch failure).\n")
            return 1

        # Phase 2: Process all validated tasks
        success_count = 0
        for task in validated_tasks:
            try:
                result = self.maniphest.edit_task_by_id(
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
                self._display_changes(f"T{task['task_id']}", result)

            except Exception as e:
                log.exception(f"Failed to edit task T{task['task_id']}")
                sys.stderr.write(f"Error editing T{task['task_id']}: {e}\n")
                # Continue processing other tasks

        print(f"\nEdited {success_count}/{len(validated_tasks)} tasks")
        return 0 if success_count == len(validated_tasks) else 1
