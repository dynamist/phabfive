# -*- coding: utf-8 -*-

# python std lib
from collections.abc import Mapping
import difflib
import fnmatch
from functools import lru_cache
import json
import logging
from pathlib import Path
import time
import datetime
from typing import Optional

# phabfive imports
from phabfive.core import Phabfive
from phabfive.constants import TICKET_PRIORITY_NORMAL
from phabfive.exceptions import (
    PhabfiveException,
    PhabfiveRemoteException,
    PhabfiveDataException,
)

# 3rd party imports
import yaml
from jinja2 import Template, Environment, meta

# phabfive transition imports - imported in cli.py where patterns are parsed

log = logging.getLogger(__name__)


class Maniphest(Phabfive):
    def __init__(self):
        super(Maniphest, self).__init__()

    def _resolve_project_phids(self, project: str) -> list[str]:
        """
        Resolve project name or wildcard pattern to list of project PHIDs.

        Parameters
        ----------
        project (str): Project name or wildcard pattern.
                      Supports: "*" (all), "prefix*", "*suffix", "*contains*"

        Returns
        -------
        list: List of project PHIDs matching the pattern. Empty list if no matches.
        """
        # Validate project parameter
        if not project or project == "":
            log.error("No project name provided. Use '*' to search all projects.")
            return []

        # Fetch all projects from Phabricator regardless of exact match or not to be able to suggest project names
        log.debug("Fetching all projects from Phabricator")

        # Use pagination to fetch all projects (API returns max 100 per page)
        all_projects = {}
        after = None

        while True:
            # Use queryKey="all" to get all projects, with cursor-based pagination
            if after:
                projects_query = self.phab.project.search(queryKey="all", after=after)
            else:
                projects_query = self.phab.project.search(queryKey="all")

            # Merge results from this page
            for p in projects_query["data"]:
                all_projects[p["fields"]["name"]] = p["phid"]

            # Check if there are more pages
            cursor = projects_query.get("cursor", {})
            after = cursor.get("after")

            if after is None:
                # No more pages
                break

        log.debug(f"Fetched {len(all_projects)} total projects from Phabricator")
        # Create case-insensitive lookup mappings
        lower_to_phid = {name.lower(): phid for name, phid in all_projects.items()}
        lower_to_original = {name.lower(): name for name in all_projects.keys()}

        # Check if wildcard search is needed
        has_wildcard = "*" in project

        if has_wildcard:
            if project == "*":
                # Search all projects
                log.info(f"Wildcard '*' matched all {len(all_projects)} projects")
                return list(all_projects.values())
            else:
                # Filter projects by wildcard pattern (case-insensitive)
                matching_projects: list[str] = [
                    lower_to_original[name_lower]
                    for name_lower in lower_to_phid.keys()
                    if fnmatch.fnmatch(name_lower, project.lower())
                ]

                if not matching_projects:
                    log.warning(f"Wildcard pattern '{project}' matched no projects")
                    return []

                log.info(
                    f"Wildcard pattern '{project}' matched {len(matching_projects)} "
                    + f"project(s): {', '.join(matching_projects)}"
                )
                return [all_projects[name] for name in matching_projects]
        # Exact match - validate project exists (case-insensitive)
        log.debug(f"Exact match mode, validating project '{project}'")

        project_lower = project.lower()
        if project_lower in lower_to_phid:
            original_name = lower_to_original[project_lower]
            log.debug(
                f"Found case-insensitive match for project '{project}' -> '{original_name}'"
            )
            return [lower_to_phid[project_lower]]
        else:
            # Project not found - suggest similar names (case-insensitive)
            cutoff = 0.6 if len(project_lower) > 3 else 0.4
            similar = difflib.get_close_matches(
                project_lower, lower_to_phid.keys(), n=3, cutoff=cutoff
            )
            if similar:
                # Map back to original names for display
                original_similar = [lower_to_original[s] for s in similar]
                log.error(
                    f"Project '{project}' not found. Did you mean: {', '.join(original_similar)}?"
                )
            else:
                log.error(f"Project '{project}' not found")
            return []

    def _fetch_project_names_for_boards(self, tasks_data):
        """Fetch project names for all boards in the task data."""
        all_board_phids = set()
        for item in tasks_data:
            boards = item.get("attachments", {}).get("columns", {}).get("boards", {})
            # Handle both dict and list formats
            if isinstance(boards, dict):
                all_board_phids.update(boards.keys())
            elif isinstance(boards, list):
                log.debug(f"Unexpected list format for boards: {boards}")
            else:
                log.debug(f"Unexpected boards type: {type(boards)}")

        if not all_board_phids:
            return {}

        projects_lookup = self.phab.project.search(
            constraints={"phids": list(all_board_phids)}
        )
        return {
            proj["phid"]: proj["fields"]["name"] for proj in projects_lookup["data"]
        }

    def _fetch_task_transactions(self, task_phid):
        """
        Fetch transaction history for a task, filtered to column changes.

        Uses the deprecated but functional maniphest.gettasktransactions API
        because the modern transaction.search API doesn't properly expose
        column transitions.

        Parameters
        ----------
        task_phid : str
            Task PHID (e.g., "PHID-TASK-...")

        Returns
        -------
        list
            List of column change transactions, each with keys:
            - oldValue: [boardPHID, columnPHID] or None
            - newValue: [boardPHID, columnPHID]
            - dateCreated: timestamp (int)
        """
        try:
            # Extract task ID from PHID
            # We need to query maniphest.search to get the ID from the PHID
            search_result = self.phab.maniphest.search(
                constraints={"phids": [task_phid]}
            )

            if not search_result.get("data"):
                log.warning(f"No task found for PHID {task_phid}")
                return []

            task_id = search_result["data"][0]["id"]

            # Use deprecated API that actually returns column transitions
            result = self.phab.maniphest.gettasktransactions(ids=[task_id])

            # Result is a dict keyed by task ID (as string)
            transactions = result.get(str(task_id), [])

            # Filter to only column transactions and transform data structure
            column_transactions = []
            for trans in transactions:
                if trans.get("transactionType") != "core:columns":
                    continue

                # Transform from old API format to expected format
                # Old: newValue[0].{boardPHID, columnPHID, fromColumnPHIDs}
                # New: {oldValue: [boardPHID, oldColumnPHID], newValue: [boardPHID, newColumnPHID]}

                new_value_data = trans.get("newValue")
                if (
                    not new_value_data
                    or not isinstance(new_value_data, list)
                    or len(new_value_data) == 0
                ):
                    continue

                move_data = new_value_data[0]
                board_phid = move_data.get("boardPHID")
                new_column_phid = move_data.get("columnPHID")
                from_columns = move_data.get("fromColumnPHIDs", {})

                # Extract old column PHID (it's a dict with column PHIDs as both keys and values)
                old_column_phid = None
                if from_columns:
                    # Get the first column PHID from the dict
                    old_column_phid = next(iter(from_columns.keys()), None)

                # Build transformed transaction
                transformed = {
                    "oldValue": [board_phid, old_column_phid]
                    if old_column_phid
                    else None,
                    "newValue": [board_phid, new_column_phid],
                    "dateCreated": int(trans.get("dateCreated", 0)),
                }

                column_transactions.append(transformed)

            log.debug(
                f"Found {len(column_transactions)} column transactions for {task_phid} (T{task_id})"
            )

            return column_transactions
        except AttributeError as e:
            # API structure changed or unexpected response format
            log.warning(
                f"Unexpected API response structure for {task_phid}: {e}. "
                "The Phabricator API format may have changed."
            )
            return []
        except KeyError as e:
            # Missing expected keys in response
            log.warning(f"Missing expected data in API response for {task_phid}: {e}")
            return []
        except Exception as e:
            # Network errors, authentication issues, or other unexpected problems
            log.warning(
                f"Failed to fetch transactions for {task_phid}: {type(e).__name__}: {e}"
            )
            return []

    def _get_column_info(self, board_phid):
        """
        Get column information for a workboard.

        Uses LRU cache (max 100 boards) to avoid repeated API calls while
        preventing unbounded memory growth in long-running sessions.

        Parameters
        ----------
        board_phid : str
            Project/board PHID

        Returns
        -------
        dict
            Mapping of column PHID to {"name": str, "sequence": int}
            Empty dict if board has no workboard or columns
        """
        return self._fetch_column_info_cached(board_phid)

    @lru_cache(maxsize=100)
    def _fetch_column_info_cached(self, board_phid):
        """
        Cached implementation of column info fetching.

        This is a separate method to enable LRU caching on an instance method.
        The cache stores up to 100 boards to balance performance and memory usage.
        """
        column_info = {}

        try:
            # Use project.column.search to get all columns for the board
            result = self.phab.project.column.search(
                constraints={"projects": [board_phid]}
            )

            if not result.get("data"):
                log.debug(f"No columns found for board {board_phid}")
                return column_info

            # Build column info mapping
            for col in result["data"]:
                column_phid = col.get("phid")
                if column_phid:
                    column_info[column_phid] = {
                        "name": col["fields"].get("name", "Unknown"),
                        "sequence": col["fields"].get(
                            "sequence", 0
                        ),  # Sequence determines order
                    }

            log.debug(f"Loaded {len(column_info)} columns for board {board_phid}")

        except Exception as e:
            log.warning(f"Failed to fetch column info for board {board_phid}: {e}")

        return column_info

    def _get_current_column(self, task, board_phid):
        """
        Get the current column name for a task on a specific board.

        Parameters
        ----------
        task : dict
            Task data from maniphest.search
        board_phid : str
            Board PHID to check

        Returns
        -------
        str or None
            Current column name, or None if task not on board
        """
        boards = task.get("attachments", {}).get("columns", {}).get("boards", {})

        if not isinstance(boards, dict):
            return None

        board_data = boards.get(board_phid)
        if not board_data:
            return None

        columns = board_data.get("columns", [])
        if not columns:
            return None

        # Get the first column (tasks are typically in one column per board)
        column_phid = columns[0].get("phid") if columns else None
        if not column_phid:
            return None

        # Get column info to resolve PHID to name
        column_info = self._get_column_info(board_phid)
        col_data = column_info.get(column_phid)

        return col_data["name"] if col_data else None

    def _task_matches_any_pattern(self, task, task_phid, patterns, board_phids):
        """
        Check if a task matches any of the given transition patterns.

        Parameters
        ----------
        task : dict
            Task data from maniphest.search
        task_phid : str
            Task PHID
        patterns : list
            List of TransitionPattern objects
        board_phids : list
            List of board PHIDs to check (typically the project being searched)

        Returns
        -------
        tuple
            (matches: bool, matched_transitions: list)
            matched_transitions contains transaction details if matches
        """
        if not patterns:
            return (True, [])  # No filtering needed

        # Fetch transaction history
        transactions = self._fetch_task_transactions(task_phid)

        if not transactions and not any(
            any(cond.get("type") == "in" for cond in p.conditions) for p in patterns
        ):
            # No transactions and no in-only patterns
            return (False, [])

        # Check each board the task is on
        for board_phid in board_phids:
            column_info = self._get_column_info(board_phid)
            current_column = self._get_current_column(task, board_phid)

            # Filter transactions to this board
            board_transactions = [
                t
                for t in transactions
                if t.get("newValue")
                and len(t["newValue"]) > 0
                and t["newValue"][0] == board_phid
            ]

            # Check if any pattern matches
            for pattern in patterns:
                if pattern.matches(board_transactions, current_column, column_info):
                    return (True, board_transactions)

        return (False, [])

    def _print_transitions(self, transactions, column_info, indent="      "):
        """
        Print transition history for a task.

        Parameters
        ----------
        transitions : list
            List of column change transactions
        column_info : dict
            Mapping of column PHID to column info
        indent : str
            Indentation string for each transition line
        """
        if not transactions:
            return

        # Sort transitions chronologically (oldest first) for better readability
        sorted_transactions = sorted(
            transactions, key=lambda t: t.get("dateCreated", 0)
        )

        for trans in sorted_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")
            date_created = trans.get("dateCreated")

            # Format timestamp
            timestamp_str = (
                format_timestamp(date_created) if date_created else "Unknown"
            )

            # Resolve column names
            if old_value and len(old_value) > 1:
                old_col_phid = old_value[1]
                old_col_info = column_info.get(old_col_phid, {})
                old_col_name = old_col_info.get("name", old_col_phid)
            else:
                old_col_name = "(new)"

            if new_value and len(new_value) > 1:
                new_col_phid = new_value[1]
                new_col_info = column_info.get(new_col_phid, {})
                new_col_name = new_col_info.get("name", new_col_phid)
            else:
                new_col_name = "Unknown"

            # Determine if forward or backward
            direction = "[•]"
            if old_value and new_value and len(old_value) > 1 and len(new_value) > 1:
                old_seq = column_info.get(old_value[1], {}).get("sequence", 0)
                new_seq = column_info.get(new_value[1], {}).get("sequence", 0)
                if new_seq > old_seq:
                    direction = "[→]"
                elif new_seq < old_seq:
                    direction = "[←]"

            print(
                f'{indent}- "{timestamp_str} {direction} {old_col_name} → {new_col_name}"'
            )

    def _print_task_boards(
        self, boards, project_phid_to_name, task_transitions_map, task_id
    ):
        """
        Print board information including columns and transitions in nested YAML format.

        Parameters
        ----------
        boards : dict
            Board data from task attachments
        project_phid_to_name : dict
            Mapping of board PHID to project name
        task_transitions_map : dict
            Mapping of task ID to transitions
        task_id : int
            Current task ID
        """
        if isinstance(boards, list):
            log.debug(f"Boards is a list (likely empty): {boards}")
            return

        if not isinstance(boards, dict) or not boards:
            return

        # Group transitions by board if available
        transitions_by_board = {}
        if task_id in task_transitions_map:
            all_transitions = task_transitions_map[task_id]
            for trans in all_transitions:
                if trans.get("newValue") and len(trans["newValue"]) > 0:
                    board_phid = trans["newValue"][0]
                    if board_phid not in transitions_by_board:
                        transitions_by_board[board_phid] = []
                    transitions_by_board[board_phid].append(trans)

        print("Boards:")
        for board_phid, board_data in boards.items():
            project_name = project_phid_to_name.get(board_phid, "Unknown")
            print(f"  {project_name}:")

            # Print column
            columns = board_data.get("columns", [])
            if columns:
                column_name = columns[0].get("name", "Unknown")
                print(f"    Column: {column_name}")

            # Print transitions for this board if available
            if board_phid in transitions_by_board:
                board_transitions = transitions_by_board[board_phid]
                if board_transitions:
                    print("    Transitions:")
                    column_info = self._get_column_info(board_phid)
                    self._print_transitions(
                        board_transitions, column_info, indent="      "
                    )

    def task_search(
        self,
        project,
        created_after=None,
        updated_after=None,
        transition_patterns=None,
        show_transitions=False,
    ):
        """
        Search for Phabricator Maniphest tasks with given parameters.

        Parameters
        ----------
        project       (str, required): Project name or wildcard pattern.
                      Supports wildcards: "*" (all), "prefix*", "*suffix", "*contains*"
                      Empty string returns no results.
        created_after (int, optional): Number of days ago the task was created.
        updated_after (int, optional): Number of days ago the task was updated.
        transition_patterns (list, optional): List of TransitionPattern objects to filter by.
        show_transitions (bool, optional): If True, display transition history for each task.
        """
        # Convert date filters to Unix timestamps
        if created_after:
            created_after = days_to_unix(created_after)
        if updated_after:
            updated_after = days_to_unix(updated_after)

        # Special case: "*" means search all projects (no filter)
        if project == "*":
            log.info("Searching across all projects (no project filter)")
            constraints = {}
            if created_after:
                constraints["createdStart"] = int(created_after)
            if updated_after:
                constraints["modifiedStart"] = int(updated_after)

            result = self.phab.maniphest.search(
                constraints=constraints, attachments={"columns": True}
            )
            result_data = result.response["data"]
        else:
            # Resolve project name/pattern to PHIDs
            project_phids = self._resolve_project_phids(project)
            if not project_phids:
                # Error already logged in _resolve_project_phids
                return

            # Handle multiple projects with OR logic (make separate calls and merge)
            if len(project_phids) > 1:
                log.info(f"Searching {len(project_phids)} projects with OR logic")
                all_tasks = {}  # task_id -> task_data
                for phid in project_phids:
                    constraints = {"projects": [phid]}
                    if created_after:
                        constraints["createdStart"] = int(created_after)
                    if updated_after:
                        constraints["modifiedStart"] = int(updated_after)

                    result = self.phab.maniphest.search(
                        constraints=constraints, attachments={"columns": True}
                    )

                    # Merge results, avoiding duplicates
                    for item in result.response["data"]:
                        task_id = item["id"]
                        if task_id not in all_tasks:
                            all_tasks[task_id] = item

                # Convert back to list for display
                result_data = list(all_tasks.values())
            else:
                # Single project
                constraints = {"projects": project_phids}
                if created_after:
                    constraints["createdStart"] = int(created_after)
                if updated_after:
                    constraints["modifiedStart"] = int(updated_after)

                result = self.phab.maniphest.search(
                    constraints=constraints, attachments={"columns": True}
                )
                result_data = result.response["data"]

        # Initialize task_transitions_map for storing transitions (used by both filtering and display)
        task_transitions_map = {}

        # Apply transition filtering if patterns specified
        if transition_patterns:
            log.info(f"Filtering {len(result_data)} tasks by transition patterns")

            # Add performance warning for large datasets
            if len(result_data) > 50:
                log.warning(
                    f"Filtering {len(result_data)} tasks may take a while as each task "
                    "requires fetching transition history from the API"
                )

            filtered_tasks = []

            # Get board PHIDs for filtering (these are the project boards we're searching)
            search_board_phids = project_phids if project != "*" else []

            for item in result_data:
                task_phid = item.get("phid")
                if not task_phid:
                    continue

                # Determine which boards this specific task is on
                current_task_boards = search_board_phids

                # If searching all projects, extract boards from task's column attachments
                if not current_task_boards:
                    boards = (
                        item.get("attachments", {}).get("columns", {}).get("boards", {})
                    )
                    if isinstance(boards, dict):
                        current_task_boards = list(boards.keys())
                    else:
                        current_task_boards = []

                matches, matched_transitions = self._task_matches_any_pattern(
                    item, task_phid, transition_patterns, current_task_boards
                )

                if matches:
                    filtered_tasks.append(item)
                    if show_transitions:
                        task_transitions_map[item["id"]] = matched_transitions

            log.info(
                f"Filtered down to {len(filtered_tasks)} tasks matching transition patterns"
            )
            result_data = filtered_tasks
        elif show_transitions:
            # Fetch transitions for all tasks when --show-transitions is used without filtering
            log.info(f"Fetching transition history for {len(result_data)} tasks")
            for item in result_data:
                task_phid = item.get("phid")
                if task_phid:
                    transactions = self._fetch_task_transactions(task_phid)
                    if transactions:
                        task_transitions_map[item["id"]] = transactions

        # Fetch project names for board display (always needed for nested format)
        project_phid_to_name = self._fetch_project_names_for_boards(result_data)

        # Display each task
        for item in result_data:
            print(f"Link: {self.url}/T{item['id']}")
            fields = item.get("fields", {})
            date_closed = ""

            for key, value in fields.items():
                if key in ["dateCreated", "dateModified"]:
                    if value:
                        formatted_time = format_timestamp(value)
                        print(f"{key[4:]}: {formatted_time}")
                elif key == "dateClosed":
                    if value:
                        date_closed = format_timestamp(value)
                        print(f"Closed: {date_closed}")
                elif key == "name":
                    print(f"Name: '{value}'" if "[" in value else f"Name: {value}")

            status_name = fields.get("status", {}).get("name", "Unknown")
            print(f"Status: {status_name} {date_closed}")

            priority_name = fields.get("priority", {}).get("name", "Unknown")
            print(f"Priority: {priority_name}")

            # Display board information (columns and transitions)
            columns_data = item.get("attachments", {}).get("columns", {})
            log.debug(f"Full columns data structure: {columns_data}")
            boards = columns_data.get("boards", {})
            log.debug(f"Boards type: {type(boards)}, value: {boards}")
            self._print_task_boards(
                boards,
                project_phid_to_name,
                task_transitions_map if show_transitions else {},
                item["id"],
            )

            description_raw = fields.get("description", {}).get("raw", "")
            if description_raw:
                print("Description: |")
                print("  >", "  > ".join(description_raw.splitlines(True)), end="")
            else:
                print("Description: ''", end="")

            print("\n")

    def _display_task_transitions(self, task_phid):
        """
        Fetch and display transition history for a single task.

        Parameters
        ----------
        task_phid : str
            Task PHID (e.g., "PHID-TASK-...")
        """
        # Fetch transitions
        transactions = self._fetch_task_transactions(task_phid)

        if not transactions:
            print("\nNo workboard transitions found.")
            return

        # Extract board PHIDs from transactions
        board_phids = set()
        for trans in transactions:
            if trans.get("newValue") and len(trans["newValue"]) > 0:
                board_phids.add(trans["newValue"][0])

        # Display transitions for each board
        print("\nBoards:")
        for board_phid in board_phids:
            # Filter transactions to this board
            board_transactions = [
                t
                for t in transactions
                if t.get("newValue")
                and len(t["newValue"]) > 0
                and t["newValue"][0] == board_phid
            ]

            if board_transactions:
                # Get column info for this board
                column_info = self._get_column_info(board_phid)

                # Get board name if possible
                try:
                    project_info = self.phab.project.search(
                        constraints={"phids": [board_phid]}
                    )
                    if project_info.get("data"):
                        board_name = project_info["data"][0]["fields"].get(
                            "name", "Unknown"
                        )
                        print(f"  {board_name}:")
                        print("    Transitions:")
                except Exception as e:
                    log.debug(f"Could not fetch board name: {e}")
                    print("  Unknown:")
                    print("    Transitions:")

                # Print the transitions
                self._print_transitions(board_transitions, column_info, indent="      ")  # noqa: F821

    def add_comment(self, ticket_identifier, comment_string):
        """
        :type ticket_identifier: str
        :type comment_string: str
        """
        result = self.phab.maniphest.edit(
            transactions=self.to_transactions({"comment": comment_string}),
            objectIdentifier=ticket_identifier,
        )

        return (True, result["object"])

    def info(self, task_id):
        """
        :type task_id: int
        """
        # FIXME: Add validation and extraction of the int part of the task_id
        result = self.phab.maniphest.info(task_id=task_id)

        return (True, result)

    def create_from_config(self, config_file, dry_run=False):
        if not config_file:
            raise PhabfiveException("Must specify a config file path")

        if not Path(config_file).is_file():
            log.error(f"Config file '{config_file}' do not exists")
            return

        with open(config_file) as stream:
            root_data = yaml.load(stream, Loader=yaml.Loader)  # nosec-B506

        # Fetch all users in phabricator, used by subscribers mapping later
        users_query = self.phab.user.search()
        username_to_id_mapping = {
            user["fields"]["username"]: user["phid"] for user in users_query["data"]
        }

        log.debug(username_to_id_mapping)

        # Fetch all projects in phabricator, used to map ticket -> projects later
        projects_query = self.phab.project.search(constraints={"name": ""})
        project_name_to_id_map = {
            project["fields"]["name"]: project["phid"]
            for project in projects_query["data"]
        }

        log.debug(project_name_to_id_map)

        # Gather and remove variables to avoid using it or polluting the data later on
        variables = root_data["variables"]
        del root_data["variables"]

        # Render variables that reference other variables using dependency resolution
        variables = _render_variables_with_dependency_resolution(variables)

        # Helper function to slim down transaction handling
        def add_transaction(t, transaction_type, value):
            t.append({"type": transaction_type, "value": value})

        def r(data_block, variable_name, variables):
            """
            Helper method to simplify Jinja2 rendering of a given value to a set of variables
            """
            data = data_block.get(variable_name, None)

            if data:
                data_block[variable_name] = Template(data).render(variables)

        def pre_process_tasks(task_config):
            """
            This is the main parser that can be run recurse in order to sort out an individual ticket and recurse down
            to pre process each task and to query all internal ID:s and update the datastructure
            """
            log.debug("Pre processing tasks")
            log.debug(task_config)

            output = task_config.copy()

            # Render strings that should be possible to render with Jinja2
            r(output, "title", variables)
            r(output, "description", variables)

            # Validate and translate project names to internal project PHID:s
            project_phids = []

            for project_name in output.get("projects", []):
                project_phid = project_name_to_id_map.get(project_name, None)

                if not project_phid:
                    raise PhabfiveRemoteException(
                        f"Project '{project_name}' is not found on the phabricator server"
                    )

                project_phids.append(project_phid)

            output["projects"] = project_phids

            # Validate and translate all subscriber users to PHID:s
            user_phids = []

            for subscriber_name in output.get("subscribers", []):
                log.debug(f"processing user {subscriber_name}")
                user_phid = username_to_id_mapping.get(subscriber_name, None)

                if not user_phid:
                    raise PhabfiveRemoteException(
                        f"Subscriber '{subscriber_name}' not found as a user on the phabricator server"
                    )

                user_phids.append(user_phid)

            output["subscribers"] = user_phids

            # Recurse down and process all child tasks
            processed_child_tasks = []
            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                processed_child_tasks = [
                    pre_process_tasks(task) for task in child_tasks
                ]

            output["tasks"] = processed_child_tasks

            return output

        def recurse_build_transactions(task_config):
            """
            This block recurses over all tasks and builds the transaction set for this ticket and stores it
            in the data structure.
            """
            log.debug("Building transactions for task_config")
            log.debug(task_config)

            # In order to not cause issues with injecting data in a recurse traversal, copy the input,
            # modify the data and return data that is later used to build a new full data structure
            output = task_config.copy()

            # # Helper lambda to slim down transaction handling
            # add_transaction = lambda t, transaction_type, value : t.append(
            #     {"type": transaction_type, "value": value},
            # )

            transactions = []

            if "title" in task_config and "description" in task_config:
                add_transaction(transactions, "title", task_config["title"])
                add_transaction(transactions, "description", task_config["description"])
                add_transaction(
                    transactions,
                    "priority",
                    task_config.get("priority", TICKET_PRIORITY_NORMAL),
                )

                projects = task_config.get("projects", [])

                if projects:
                    add_transaction(transactions, "projects.set", projects)

                subscribers = task_config.get("subscribers", [])

                if subscribers:
                    add_transaction(transactions, "subscribers.set", subscribers)

                # Prepare all parent and subtasks, and check if we have a parent task from the config file
                subtasks = task_config.get("subtasks", [])

                if subtasks:
                    subtasks_phids = []

                    for ticket_id in subtasks:
                        search_result = self.phab.maniphest.search(
                            constraints={"ids": [int(ticket_id[1:])]},
                        )

                        if len(search_result["data"]) != 1:
                            raise PhabfiveRemoteException(
                                f"Unable to find subtask ticket in phabricator instance with ID={ticket_id}"
                            )

                        subtasks_phids.append(search_result["data"][0]["phid"])

                    add_transaction(transactions, "subtasks.set", subtasks_phids)

                parents = task_config.get("parents", [])

                if parents:
                    parent_phids = []

                    for ticket_id in parents:
                        search_result = self.phab.maniphest.search(
                            constraints={"ids": [int(ticket_id[1:])]},
                        )

                        if len(search_result["data"]) != 1:
                            raise PhabfiveRemoteException(
                                f"Unable to find parent ticket in phabricator instance with ID={ticket_id}"
                            )

                        parent_phids.append(search_result["data"][0]["phid"])

                    add_transaction(transactions, "parents.set", parent_phids)
            else:
                log.warning(
                    "Required fields 'title' and 'description' is not present in this data block, skipping ticket creation"
                )

            output["transactions"] = transactions

            processed_child_tasks = []
            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                # If there is child tasks to create, recurse down to all of them one by one
                processed_child_tasks = [
                    recurse_build_transactions(task) for task in child_tasks
                ]
            else:
                processed_child_tasks = []

            output["tasks"] = processed_child_tasks

            return output

        def recurse_commit_transactions(task_config, parent_task_config):
            """
            This recurse functions purpose is to iterate over all tickets, commit them to phabricator
            and link them to eachother via the ticket hiearchy or explicit parent/subtask links.

            task_config is the current task to create and the parent_task_config is if we have a tree
            of tickets defined in our config file.
            """
            log.debug("\n -- Commiting task")
            log.debug(json.dumps(task_config, indent=2))
            log.debug(" ** parent block")
            log.debug(json.dumps(parent_task_config, indent=2))

            transactions_to_commit = task_config.get("transactions", [])

            if transactions_to_commit:
                # Parent ticket based on the task hiearchy defined in the config file we parsed is different
                # from the explicit "ticket parent" that can be defined
                if parent_task_config and "phid" in parent_task_config:
                    add_transaction(
                        transactions_to_commit,
                        "parents.add",
                        [parent_task_config["phid"]],
                    )

                log.debug(" -- transactions to commit")
                log.debug(transactions_to_commit)

                if dry_run:
                    log.critical(
                        "Running with --dry-run, tickets !!WILL NOT BE!! commited to phabricator"
                    )
                else:
                    result = self.phab.maniphest.edit(
                        transactions=transactions_to_commit,
                    )

                    # Store the newly created ticket ID in the data structure so child tickets can look it up
                    task_config["phid"] = str(result["object"]["phid"])
            else:
                log.warning(
                    "No transactions to commit here, either a bug or root object that can't be transacted"
                )

            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                for child_task in child_tasks:
                    recurse_commit_transactions(child_task, task_config)

        # Main task recursion logic
        if "tasks" not in root_data:
            raise PhabfiveDataException(
                "Config file must contain keyword tasks in the root"
            )

        pre_process_output = pre_process_tasks(root_data)
        log.debug("Final pre_process_output")
        log.debug(json.dumps(pre_process_output, indent=2))
        log.debug("\n----------------\n")

        parsed_root_data = recurse_build_transactions(pre_process_output)
        log.debug(" -- Final built transactions")
        log.debug(json.dumps(parsed_root_data, indent=2))
        log.debug(" -- transactions for all tickets")
        log.debug(parsed_root_data)
        log.debug("\n")

        # Always start with a blank parent
        recurse_commit_transactions(parsed_root_data, None)


def days_to_unix(days):
    """
    Convert days into a UNIX timestamp.
    """
    seconds = int(days) * 24 * 3600
    return int(time.time()) - seconds


def format_timestamp(timestamp):
    """
    Convert UNIX timestamp to ISO 8601 string (readable time format).
    """
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _extract_variable_dependencies(template_str: str) -> set[str]:
    """
    Extract variable names referenced in a Jinja2 template string.
    """
    try:
        env = Environment()
        ast = env.parse(template_str)
        return meta.find_undeclared_variables(ast)
    except Exception as e:
        log.warning(f"Failed to parse template '{template_str}': {e}")
        return set()


def _build_dependency_graph(variables: Mapping[str, object]) -> dict[str, set[str]]:
    """
    Build a dependency graph mapping each variable to its dependencies.
    """
    graph: dict[str, set[str]] = {}

    for var_name, var_value in variables.items():
        if isinstance(var_value, str):
            # Extract dependencies and filter to only include string variables
            # (non-strings don't need rendering, so no ordering constraint)
            dependencies = _extract_variable_dependencies(var_value)
            graph[var_name] = {
                dep
                for dep in dependencies
                if dep in variables and isinstance(variables[dep], str)
            }
        else:
            # Non-string values have no dependencies
            graph[var_name] = set()

    return graph


def _detect_circular_dependencies(graph: dict[str, set[str]]) -> tuple[bool, list[str]]:
    """
    Detect circular dependencies using depth-first search.
    Returns (has_cycle, cycle_path) tuple.
    """
    visited: set[str] = set()
    recursion_stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> Optional[list[str]]:
        visited.add(node)
        recursion_stack.add(node)
        path.append(node)

        for dependency in graph.get(node, set()):
            if dependency not in visited:
                cycle = dfs(dependency)
                if cycle is not None:
                    return cycle
            elif dependency in recursion_stack:
                # Found a cycle - build the cycle path
                cycle_start_idx = path.index(dependency)
                cycle_path = path[cycle_start_idx:] + [dependency]
                return cycle_path

        _ = path.pop()
        recursion_stack.remove(node)
        return None

    for node in graph:
        if node not in visited:
            result = dfs(node)
            if result is not None:
                return (True, result)

    return (False, [])


def _topological_sort(graph: dict[str, set[str]]) -> list[str]:
    """
    Perform topological sort using DFS.
    Returns variables in dependency order (dependencies before dependents).
    """
    visited: set[str] = set()
    result: list[str] = []

    def dfs(node: str) -> None:
        visited.add(node)

        # Visit all dependencies first
        for dependency in graph.get(node, set()):
            if dependency not in visited:
                dfs(dependency)

        # Add current node after all dependencies
        result.append(node)

    for node in graph:
        if node not in visited:
            dfs(node)

    return result


def _render_variables_with_dependency_resolution(
    variables: Mapping[str, object],
) -> dict[str, object]:
    """
    Render Jinja2 template variables with proper dependency resolution.

    Analyzes variable dependencies, detects circular references, performs topological
    sorting, and renders variables in the correct order so all dependencies are
    resolved before being referenced.

    Raises
    ------
    PhabfiveDataException
        If circular dependencies are detected in the variable definitions.
    """
    log.debug("Building dependency graph for variables")
    graph = _build_dependency_graph(variables)
    log.debug(
        f"Dependency graph: {json.dumps({k: list(v) for k, v in graph.items()}, indent=2)}"
    )

    # Detect circular dependencies
    has_cycle, cycle_path = _detect_circular_dependencies(graph)
    if has_cycle:
        cycle_str = " → ".join(cycle_path)
        raise PhabfiveDataException(
            f"Circular reference detected in variables: {cycle_str}"
        )

    # Sort variables in dependency order
    sorted_vars = _topological_sort(graph)
    log.debug(f"Topologically sorted variables: {sorted_vars}")

    # Render variables in order
    rendered = variables.copy()
    for var_name in sorted_vars:
        var_value = rendered[var_name]
        if isinstance(var_value, str):
            rendered[var_name] = Template(var_value).render(rendered)
            log.debug(f"Rendered variable '{var_name}': {rendered[var_name]}")

    return rendered
