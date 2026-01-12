# -*- coding: utf-8 -*-

"""Main Maniphest class that orchestrates all submodules."""

import itertools
import json
import logging
from functools import lru_cache
from pathlib import Path

from jinja2 import Template
from ruamel.yaml import YAML

from phabfive.constants import TICKET_PRIORITY_NORMAL
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveException,
    PhabfiveRemoteException,
)
from phabfive.maniphest.fetchers import (
    fetch_all_transactions,
    fetch_project_names_for_boards,
    get_api_priority_map,
    get_api_status_map,
)
from phabfive.maniphest.filters import (
    task_matches_any_pattern,
    task_matches_priority_patterns,
    task_matches_project_patterns,
    task_matches_status_patterns,
)
from phabfive.maniphest.formatters import build_task_boards, build_task_display_data
from phabfive.maniphest.resolvers import (
    parse_plus_separated,
    resolve_project_phids,
    resolve_project_phids_for_create,
    resolve_user_phid,
    resolve_user_phids,
)
from phabfive.maniphest.utils import (
    days_ago_to_timestamp,
    render_variables_with_dependency_resolution,
)
from phabfive.maniphest.validators import validate_priority, validate_status
from phabfive.project_filters import parse_project_patterns

log = logging.getLogger(__name__)


class Maniphest(Phabfive):
    def __init__(self):
        super(Maniphest, self).__init__()

    # Wrapper methods that delegate to submodules while maintaining self.phab access

    def _resolve_project_phids(self, project: str) -> list[str]:
        """Resolve project name, hashtag, or wildcard pattern to list of project PHIDs."""
        return resolve_project_phids(self.phab, project)

    def _parse_plus_separated(self, values):
        """Parse plus-separated values from CLI options."""
        return parse_plus_separated(values)

    def _validate_priority(self, priority):
        """Validate and normalize priority value."""
        return validate_priority(priority)

    def _validate_status(self, status):
        """Validate and normalize status value."""
        return validate_status(status, self._get_api_status_map())

    def _resolve_user_phid(self, username):
        """Resolve a single username to PHID."""
        return resolve_user_phid(self.phab, username)

    def _resolve_user_phids(self, usernames):
        """Resolve multiple usernames to PHIDs."""
        return resolve_user_phids(self.phab, usernames)

    def _resolve_project_phids_for_create(self, project_names):
        """Resolve project names to PHIDs and slugs for task creation."""
        return resolve_project_phids_for_create(self.phab, project_names)

    def _fetch_project_names_for_boards(self, tasks_data):
        """Fetch project names for all boards in the task data."""
        return fetch_project_names_for_boards(self.phab, tasks_data)

    def _build_task_boards(self, boards, project_phid_to_name):
        """Build board information dict with current columns only."""
        return build_task_boards(boards, project_phid_to_name)

    @lru_cache(maxsize=1)
    def _get_api_priority_map(self):
        """Get mapping from Phabricator API numeric values to human-readable priority names."""
        return get_api_priority_map()

    @lru_cache(maxsize=1)
    def _get_api_status_map(self):
        """Get status information from Phabricator API."""
        return get_api_status_map(self.phab)

    def _fetch_all_transactions(
        self,
        task_phid,
        need_columns=False,
        need_priority=False,
        need_status=False,
        need_assignee=False,
        need_comments=False,
    ):
        """Fetch all transaction types for a task in a single API call."""
        return fetch_all_transactions(
            self.phab,
            task_phid,
            self._get_api_priority_map,
            need_columns=need_columns,
            need_priority=need_priority,
            need_status=need_status,
            need_assignee=need_assignee,
            need_comments=need_comments,
        )

    def _task_matches_priority_patterns(
        self, task, task_phid, priority_patterns, transactions=None
    ):
        """Check if a task matches any of the given priority patterns."""
        return task_matches_priority_patterns(
            self.phab,
            task,
            task_phid,
            priority_patterns,
            self._get_api_priority_map,
            transactions=transactions,
        )

    def _task_matches_status_patterns(
        self, task, task_phid, status_patterns, transactions=None
    ):
        """Check if a task matches any of the given status patterns."""
        return task_matches_status_patterns(
            self.phab,
            task,
            task_phid,
            status_patterns,
            self._get_api_priority_map,
            transactions=transactions,
        )

    def _task_matches_project_patterns(
        self, task, project_patterns, resolved_phids_by_pattern
    ):
        """Check if a task matches the project filter criteria."""
        return task_matches_project_patterns(
            task, project_patterns, resolved_phids_by_pattern
        )

    def _task_matches_any_pattern(
        self, task, task_phid, patterns, board_phids, transactions=None
    ):
        """Check if a task matches any of the given transition patterns."""
        return task_matches_any_pattern(
            self.phab,
            task,
            task_phid,
            patterns,
            board_phids,
            self._get_api_priority_map,
            transactions=transactions,
        )

    def _build_task_display_data(
        self,
        result_data,
        task_transitions_map=None,
        priority_transitions_map=None,
        status_transitions_map=None,
        assignee_transitions_map=None,
        comments_map=None,
        matching_boards_map=None,
        matching_priority_map=None,
        matching_status_map=None,
        show_history=False,
        show_metadata=False,
        show_comments=False,
    ):
        """Build structured task display data from API results."""
        return build_task_display_data(
            self.phab,
            self.url,
            self.format_link,
            self.format_direction,
            self._get_api_status_map,
            result_data,
            task_transitions_map=task_transitions_map,
            priority_transitions_map=priority_transitions_map,
            status_transitions_map=status_transitions_map,
            assignee_transitions_map=assignee_transitions_map,
            comments_map=comments_map,
            matching_boards_map=matching_boards_map,
            matching_priority_map=matching_priority_map,
            matching_status_map=matching_status_map,
            show_history=show_history,
            show_metadata=show_metadata,
            show_comments=show_comments,
        )

    def parse_status_patterns_with_api(self, patterns_str):
        """
        Parse status patterns with API-fetched status ordering.

        This is a wrapper that fetches status information from the Phabricator API
        and passes it to parse_status_patterns for dynamic status ordering.

        Parameters
        ----------
        patterns_str : str
            Pattern string like "from:Open:raised+in:Resolved,to:Closed"

        Returns
        -------
        list
            List of StatusPattern objects

        Raises
        ------
        PhabfiveException
            If pattern syntax is invalid
        """
        from phabfive.status_transitions import parse_status_patterns

        api_response = self._get_api_status_map()
        return parse_status_patterns(patterns_str, api_response)

    def task_show(
        self, task_id, show_history=False, show_metadata=False, show_comments=False
    ):
        """
        Show a single Phabricator Maniphest task with optional history and metadata.

        This method uses the same display format as task_search() for consistency.

        Parameters
        ----------
        task_id : int
            Task ID (e.g., 123 for T123)
        show_history : bool, optional
            If True, display column, priority, and status transition history
        show_metadata : bool, optional
            If True, display metadata (mainly useful for debugging, less useful for single task)
        show_comments : bool, optional
            If True, display comments on the task
        """
        # Use maniphest.search API to fetch the task
        result = self.phab.maniphest.search(
            constraints={"ids": [task_id]}, attachments={"columns": True}
        )

        result_data = result.response.get("data", [])

        if not result_data:
            log.error(f"Task T{task_id} not found")
            return

        # Initialize maps for storing transitions, assignee, and comments history
        task_transitions_map = {}
        priority_transitions_map = {}
        status_transitions_map = {}
        assignee_transitions_map = {}
        comments_map = {}

        # Fetch transaction data if any transaction-based info is requested
        if show_history or show_comments:
            task_phid = result_data[0].get("phid")
            if task_phid:
                log.debug(f"Fetching transactions for T{task_id}")
                # Fetch all relevant transaction types in a single API call
                all_fetched_transactions = self._fetch_all_transactions(
                    task_phid,
                    need_columns=show_history,
                    need_priority=show_history,
                    need_status=show_history,
                    need_assignee=show_history,
                    need_comments=show_comments,
                )
                # Store transactions for history display
                if all_fetched_transactions.get("columns"):
                    task_transitions_map[task_id] = all_fetched_transactions["columns"]
                if all_fetched_transactions.get("priority"):
                    priority_transitions_map[task_id] = all_fetched_transactions[
                        "priority"
                    ]
                if all_fetched_transactions.get("status"):
                    status_transitions_map[task_id] = all_fetched_transactions["status"]
                if all_fetched_transactions.get("assignee"):
                    assignee_transitions_map[task_id] = all_fetched_transactions[
                        "assignee"
                    ]
                if all_fetched_transactions.get("comments"):
                    comments_map[task_id] = all_fetched_transactions["comments"]

        # Use shared method to build task data
        return self._build_task_display_data(
            result_data,
            task_transitions_map=task_transitions_map,
            priority_transitions_map=priority_transitions_map,
            status_transitions_map=status_transitions_map,
            assignee_transitions_map=assignee_transitions_map,
            comments_map=comments_map,
            show_history=show_history,
            show_metadata=show_metadata,
            show_comments=show_comments,
        )

    def _load_search_config(self, template_path):
        """
        Load search parameters from a YAML file (supports multi-document).

        Parameters
        ----------
        template_path : str
            Path to the YAML template file containing search parameters

        Returns
        -------
        list
            List of dictionaries, each containing search parameters for one search.
            Single document files return a list with one element.

        Raises
        ------
        PhabfiveException
            If the template file is invalid or contains unsupported parameters
        """
        template_file = Path(template_path)

        if not template_file.exists():
            raise PhabfiveException(f"Template file not found: {template_path}")

        if not template_file.is_file():
            raise PhabfiveException(f"Path is not a file: {template_path}")

        try:
            with open(template_file, "r", encoding="utf-8") as f:
                yaml_loader = YAML()
                # Load all documents from the YAML file
                documents = list(yaml_loader.load_all(f))
        except Exception as e:
            raise PhabfiveException(
                f"Failed to parse template file {template_path}: {e}"
            )

        if not documents:
            raise PhabfiveException("Template file contains no documents")

        search_configs = []
        supported_params = {
            "text_query",
            "tag",
            "created-after",
            "updated-after",
            "column",
            "priority",
            "status",
            "show-history",
            "show-metadata",
        }

        for i, data in enumerate(documents):
            if not isinstance(data, dict):
                raise PhabfiveException(
                    f"Document {i + 1} in YAML file must contain a dictionary at root level"
                )

            search_params = data.get("search", {})
            if not isinstance(search_params, dict):
                raise PhabfiveException(
                    f"Document {i + 1}: 'search' section must be a dictionary"
                )

            # Validate supported parameters
            invalid_params = set(search_params.keys()) - supported_params
            if invalid_params:
                raise PhabfiveException(
                    f"Document {i + 1}: Unsupported search parameters: {', '.join(invalid_params)}. "
                    f"Supported: {', '.join(sorted(supported_params))}"
                )

            # Store the search config with optional title and description
            config = {
                "search": search_params,
                "title": data.get("title", f"Search {i + 1}"),
                "description": data.get("description", None),
            }
            search_configs.append(config)

        log.info(
            f"Loaded {len(search_configs)} search configuration(s) from {template_path}"
        )
        for i, config in enumerate(search_configs):
            log.debug(f"Search {i + 1} parameters: {config['search']}")

        return search_configs

    def task_search(
        self,
        text_query=None,
        tag=None,
        created_after=None,
        updated_after=None,
        transition_patterns=None,
        priority_patterns=None,
        status_patterns=None,
        show_history=False,
        show_metadata=False,
    ):
        """
        Search for Phabricator Maniphest tasks with given parameters.

        Parameters
        ----------
        text_query    (str, optional): Free-text search in task title/description.
                      Uses Phabricator's query constraint.
        tag           (str, optional): Project name, wildcard pattern, or filter pattern.
                      Supports wildcards: "*" (all), "prefix*", "*suffix", "*contains*"
                      Supports filter syntax: "ProjectA,ProjectB" (OR), "ProjectA+ProjectB" (AND)
                      If None, no project filtering is applied.
        created_after (int, optional): Number of days ago the task was created.
        updated_after (int, optional): Number of days ago the task was updated.
        transition_patterns (list, optional): List of TransitionPattern objects to filter by.
                      Filters tasks based on column transitions (from, to, in, been, never, forward, backward).
        priority_patterns (list, optional): List of PriorityPattern objects to filter by.
                      Filters tasks based on priority transitions (from, to, in, been, never, raised, lowered).
        status_patterns (list, optional): List of StatusPattern objects to filter by.
                      Filters tasks based on status transitions (from, to, in, been, never, raised, lowered).
        show_history (bool, optional): If True, display column, priority, and status transition history for each task.
                      Must be explicitly requested; not auto-enabled by filters.
        show_metadata (bool, optional): If True, display which boards/priorities/statuses matched the filters.
                      Shows MatchedBoards list, MatchedPriority, and MatchedStatus boolean for debugging filter logic.
        """
        # Validation - require at least one filter
        has_any_filter = any(
            [
                text_query,
                tag,
                created_after,
                updated_after,
                transition_patterns,
                priority_patterns,
                status_patterns,
            ]
        )

        if not has_any_filter:
            raise PhabfiveConfigException("No search criteria specified")

        # Convert date filters to Unix timestamps (preserve original day values for logging)
        created_after_days = created_after
        updated_after_days = updated_after
        if created_after:
            created_after = days_ago_to_timestamp(created_after)
        if updated_after:
            updated_after = days_ago_to_timestamp(updated_after)

        project_patterns = None
        project_phids = []
        resolved_phids_by_pattern = []

        if tag and tag != "*":
            if "," in tag or "+" in tag:
                try:
                    project_patterns = parse_project_patterns(tag)
                    log.debug(
                        f"Parsed {len(project_patterns)} tag patterns from '{tag}'"
                    )

                    # Resolve each project name/wildcard in all patterns to PHIDs
                    # Keep track of which PHIDs belong to which pattern for AND/OR logic
                    resolved_phids_set = set()
                    resolved_phids_by_pattern = []

                    for pattern in project_patterns:
                        # Resolve all project names in this pattern to PHIDs
                        phids_by_name = []
                        for project_name in pattern.project_names:
                            phids = self._resolve_project_phids(project_name)
                            if not phids:
                                log.error(
                                    f"No projects matched '{project_name}' in tag pattern '{tag}'"
                                )
                                return
                            phids_by_name.append(phids)

                        # For AND logic (multiple project names): create cartesian product
                        # For OR logic (single project name): just flatten the list
                        if len(pattern.project_names) > 1:
                            # AND logic: create all combinations (cartesian product)
                            combinations = list(itertools.product(*phids_by_name))
                            # Store as tuples that must all be in task's projectPHIDs
                            resolved_phids_by_pattern.append(combinations)
                        else:
                            # OR logic: just a flat list of PHIDs (from wildcard expansion)
                            resolved_phids_by_pattern.append(phids_by_name[0])

                        # Add all PHIDs to the set for fetching
                        for phid_list in phids_by_name:
                            resolved_phids_set.update(phid_list)

                    project_phids = list(resolved_phids_set)

                    if not project_phids:
                        log.error(f"No projects matched the tag pattern '{tag}'")
                        return

                    # Determine AND vs OR logic for logging
                    has_and_patterns = any(
                        len(p.project_names) > 1 for p in project_patterns
                    )
                    logic_type = "AND" if has_and_patterns else "OR"
                    log.info(
                        f"Tag pattern '{tag}' resolved to {len(project_phids)} project(s) with {logic_type} logic"
                    )
                except PhabfiveException as e:
                    log.error(f"Invalid tag pattern: {e}")
                    return
            else:
                project_phids = self._resolve_project_phids(tag)
                if not project_phids:
                    # Error already logged in _resolve_project_phids
                    return

        if tag == "*" or tag is None:
            if tag == "*":
                log.info("Searching across all projects (tag='*', no project filter)")
            else:
                log.info("No tag specified, searching across all projects")
            constraints = {}

            if text_query:
                log.info(f"Free-text search: '{text_query}'")
                # Note: maniphest.search doesn't have a fullText constraint
                # We use the 'query' constraint which searches titles and descriptions
                constraints["query"] = text_query

            if created_after:
                constraints["createdStart"] = int(created_after)
            if updated_after:
                constraints["modifiedStart"] = int(updated_after)

            # Use pagination to fetch all tasks (API returns max 100 per page)
            result_data = []
            after = None

            while True:
                if after:
                    result = self.phab.maniphest.search(
                        constraints=constraints,
                        attachments={"columns": True},
                        after=after,
                    )
                else:
                    result = self.phab.maniphest.search(
                        constraints=constraints, attachments={"columns": True}
                    )

                # Accumulate results from this page
                result_data.extend(result.response["data"])

                # Check if there are more pages
                cursor = result.get("cursor", {})
                after = cursor.get("after")
                log.debug(
                    f"Fetched page with {len(result.response['data'])} tasks, total so far: {len(result_data)}, next cursor: {after}"
                )

                if after is None:
                    # No more pages
                    break
        else:
            # Handle multiple projects (make separate calls and merge)
            if len(project_phids) > 1:
                all_tasks = {}  # task_id -> task_data
                for phid in project_phids:
                    constraints = {"projects": [phid]}

                    if text_query:
                        constraints["query"] = text_query

                    if created_after:
                        constraints["createdStart"] = int(created_after)
                    if updated_after:
                        constraints["modifiedStart"] = int(updated_after)

                    # Use pagination for each project (API returns max 100 per page)
                    after = None
                    while True:
                        if after:
                            result = self.phab.maniphest.search(
                                constraints=constraints,
                                attachments={"columns": True},
                                after=after,
                            )
                        else:
                            result = self.phab.maniphest.search(
                                constraints=constraints, attachments={"columns": True}
                            )

                        # Merge results from this page, avoiding duplicates
                        for item in result.response["data"]:
                            task_id = item["id"]
                            if task_id not in all_tasks:
                                all_tasks[task_id] = item

                        # Check if there are more pages for this project
                        cursor = result.get("cursor", {})
                        after = cursor.get("after")
                        log.debug(
                            f"Project {phid}: fetched page with {len(result.response['data'])} tasks, total unique: {len(all_tasks)}, next cursor: {after}"
                        )

                        if after is None:
                            # No more pages for this project
                            break

                # Convert back to list for display
                result_data = list(all_tasks.values())
            else:
                # Single project
                constraints = {"projects": project_phids}

                if text_query:
                    constraints["query"] = text_query

                if created_after:
                    constraints["createdStart"] = int(created_after)
                if updated_after:
                    constraints["modifiedStart"] = int(updated_after)

                # Use pagination to fetch all tasks (API returns max 100 per page)
                result_data = []
                after = None

                while True:
                    if after:
                        result = self.phab.maniphest.search(
                            constraints=constraints,
                            attachments={"columns": True},
                            after=after,
                        )
                    else:
                        result = self.phab.maniphest.search(
                            constraints=constraints, attachments={"columns": True}
                        )

                    # Accumulate results from this page
                    result_data.extend(result.response["data"])

                    # Check if there are more pages
                    cursor = result.get("cursor", {})
                    after = cursor.get("after")
                    log.debug(
                        f"Fetched page with {len(result.response['data'])} tasks, total so far: {len(result_data)}, next cursor: {after}"
                    )

                    if after is None:
                        # No more pages
                        break

        # Initialize task_transitions_map for storing transitions (used by both filtering and display)
        task_transitions_map = {}
        # Initialize priority_transitions_map for storing priority history
        priority_transitions_map = {}
        # Initialize status_transitions_map for storing status history
        status_transitions_map = {}
        # Initialize matching_boards_map for storing which boards matched the filter
        matching_boards_map = {}
        # Initialize matching_priority_map for storing whether priority filter matched
        matching_priority_map = {}
        # Initialize matching_status_map for storing whether status filter matched
        matching_status_map = {}

        # Determine which transaction types are needed before the loop
        # This allows us to fetch once per task instead of multiple times
        need_columns = bool(transition_patterns) or show_history
        need_priority = bool(priority_patterns) or show_history
        need_status = bool(status_patterns) or show_history

        # Apply transition filtering if patterns specified
        if (
            transition_patterns
            or priority_patterns
            or status_patterns
            or project_patterns
        ):
            filter_desc = []
            if text_query:
                filter_desc.append(f"query='{text_query}'")
            if tag:
                filter_desc.append(f"tag='{tag}'")
            if created_after:
                filter_desc.append(f"created-after={created_after_days}d")
            if updated_after:
                filter_desc.append(f"updated-after={updated_after_days}d")
            if transition_patterns:
                col_strs = [str(p) for p in transition_patterns]
                filter_desc.append(f"column='{','.join(col_strs)}'")
            if priority_patterns:
                pri_strs = [str(p) for p in priority_patterns]
                filter_desc.append(f"priority='{','.join(pri_strs)}'")
            if status_patterns:
                stat_strs = [str(p) for p in status_patterns]
                filter_desc.append(f"status='{','.join(stat_strs)}'")
            # Note: project_patterns is derived from tag, so not shown separately
            log.info(f"Filtering {len(result_data)} tasks by {', '.join(filter_desc)}")

            # Add performance warning for large datasets
            if len(result_data) > 50:
                log.warning(
                    f"Filtering {len(result_data)} tasks may take a while as each task "
                    "requires fetching transition history from the API"
                )

            filtered_tasks = []

            # Get board PHIDs for filtering (these are the project boards we're searching)
            search_board_phids = project_phids if tag and tag != "*" else []

            for item in result_data:
                task_phid = item.get("phid")
                if not task_phid:
                    continue

                # Fetch all transaction types in one API call if any are needed
                all_fetched_transactions = None
                if need_columns or need_priority or need_status:
                    all_fetched_transactions = self._fetch_all_transactions(
                        task_phid,
                        need_columns=need_columns,
                        need_priority=need_priority,
                        need_status=need_status,
                    )

                # Check column transition patterns
                column_matches = True
                all_transitions = []
                matching_board_phids = set()

                if transition_patterns:
                    # Determine which boards this specific task is on
                    current_task_boards = search_board_phids

                    # If searching all projects, extract boards from task's column attachments
                    if not current_task_boards:
                        boards = (
                            item.get("attachments", {})
                            .get("columns", {})
                            .get("boards", {})
                        )
                        if isinstance(boards, dict):
                            current_task_boards = list(boards.keys())
                        else:
                            current_task_boards = []

                    column_matches, all_transitions, matching_board_phids = (
                        self._task_matches_any_pattern(
                            item,
                            task_phid,
                            transition_patterns,
                            current_task_boards,
                            transactions=all_fetched_transactions,
                        )
                    )

                # Check priority patterns and fetch priority history if needed
                priority_matches = True
                priority_trans = []

                if priority_patterns:
                    # Filtering by priority - check if task matches
                    priority_matches, priority_trans = (
                        self._task_matches_priority_patterns(
                            item,
                            task_phid,
                            priority_patterns,
                            transactions=all_fetched_transactions,
                        )
                    )
                elif show_history and all_fetched_transactions:
                    # Not filtering by priority, but need history for display
                    priority_trans = all_fetched_transactions.get("priority", [])

                # Check status patterns and fetch status history if needed
                status_matches = True
                status_trans = []

                if status_patterns:
                    # Filtering by status - check if task matches
                    status_matches, status_trans = self._task_matches_status_patterns(
                        item,
                        task_phid,
                        status_patterns,
                        transactions=all_fetched_transactions,
                    )
                elif show_history and all_fetched_transactions:
                    # Not filtering by status, but need history for display
                    status_trans = all_fetched_transactions.get("status", [])

                # Check project patterns
                project_matches = True

                if project_patterns:
                    # Filtering by project - check if task matches
                    project_matches = self._task_matches_project_patterns(
                        item,
                        project_patterns,
                        resolved_phids_by_pattern,
                    )

                # Task must match column AND priority AND status AND project patterns (if specified)
                if (
                    column_matches
                    and priority_matches
                    and status_matches
                    and project_matches
                ):
                    filtered_tasks.append(item)
                    if show_history:
                        if all_transitions:
                            task_transitions_map[item["id"]] = all_transitions
                        if priority_trans:
                            priority_transitions_map[item["id"]] = priority_trans
                        if status_trans:
                            status_transitions_map[item["id"]] = status_trans
                    # Store which boards matched for this task
                    if matching_board_phids:
                        matching_boards_map[item["id"]] = matching_board_phids
                    # Store whether priority filter matched
                    if priority_patterns:
                        matching_priority_map[item["id"]] = priority_matches
                    # Store whether status filter matched
                    if status_patterns:
                        matching_status_map[item["id"]] = status_matches

            log.info(
                f"Found {len(filtered_tasks)} matches out of {len(result_data)} tasks in {len(project_phids)} project(s)"
            )
            result_data = filtered_tasks
        elif show_history:
            # Fetch transitions for all tasks when --show-history is used without filtering
            log.info(f"Fetching transition history for {len(result_data)} tasks")
            for item in result_data:
                task_phid = item.get("phid")
                if task_phid:
                    # Fetch all transaction types in a single API call
                    all_fetched_transactions = self._fetch_all_transactions(
                        task_phid,
                        need_columns=True,
                        need_priority=True,
                        need_status=True,
                    )
                    # Store transactions for history display
                    if all_fetched_transactions.get("columns"):
                        task_transitions_map[item["id"]] = all_fetched_transactions[
                            "columns"
                        ]
                    if all_fetched_transactions.get("priority"):
                        priority_transitions_map[item["id"]] = all_fetched_transactions[
                            "priority"
                        ]
                    if all_fetched_transactions.get("status"):
                        status_transitions_map[item["id"]] = all_fetched_transactions[
                            "status"
                        ]

        # Use shared method to build task data
        return self._build_task_display_data(
            result_data,
            task_transitions_map=task_transitions_map,
            priority_transitions_map=priority_transitions_map,
            status_transitions_map=status_transitions_map,
            matching_boards_map=matching_boards_map,
            matching_priority_map=matching_priority_map,
            matching_status_map=matching_status_map,
            show_history=show_history,
            show_metadata=show_metadata,
        )

    def add_task_comment(self, ticket_identifier, comment_string):
        """
        :type ticket_identifier: str
        :type comment_string: str
        """
        result = self.phab.maniphest.edit(
            transactions=self.to_transactions({"comment": comment_string}),
            objectIdentifier=ticket_identifier,
        )

        return (True, result["object"])

    def get_task_info(self, task_id):
        """
        :type task_id: int
        """
        # FIXME: Add validation and extraction of the int part of the task_id
        result = self.phab.maniphest.info(task_id=task_id)

        return (True, result)

    def create_tasks_from_yaml(self, create_config, dry_run=False):
        if not create_config:
            raise PhabfiveException("Must specify a config file path")

        if not Path(create_config).is_file():
            log.error(f"Config file '{create_config}' do not exists")
            return

        with open(create_config) as stream:
            yaml_loader = YAML()
            root_data = yaml_loader.load(stream)

        if dry_run:
            log.warning("DRY RUN: Tasks will not be created in Phabricator")

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
        variables = render_variables_with_dependency_resolution(variables)

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
            elif "tasks" not in task_config:
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

        # List to collect dry-run tasks (nonlocal to be accessible in nested function)
        dry_run_tasks = []

        def recurse_commit_transactions(task_config, parent_task_config, depth=0):
            """
            This recurse functions purpose is to iterate over all tickets, commit them to phabricator
            and link them to eachother via the ticket hiearchy or explicit parent/subtask links.

            task_config is the current task to create and the parent_task_config is if we have a tree
            of tickets defined in our config file.
            """
            nonlocal dry_run_tasks

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
                    # Extract title from transactions for display
                    title = next(
                        (
                            t["value"]
                            for t in transactions_to_commit
                            if t["type"] == "title"
                        ),
                        "<no title>",
                    )
                    dry_run_tasks.append({"depth": depth, "title": title})
                else:
                    result = self.phab.maniphest.edit(
                        transactions=transactions_to_commit,
                    )

                    # Store the newly created ticket ID in the data structure so child tickets can look it up
                    task_config["phid"] = str(result["object"]["phid"])
            child_tasks = task_config.get("tasks", None)

            if not transactions_to_commit and not child_tasks:
                log.warning(
                    "No transactions to commit and no child tasks - possible data issue"
                )

            if child_tasks:
                for child_task in child_tasks:
                    recurse_commit_transactions(child_task, task_config, depth + 1)

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

        # Return dry-run data if in dry-run mode
        if dry_run:
            return {"dry_run": True, "tasks": dry_run_tasks}

        return {
            "dry_run": False,
            "created_count": len(dry_run_tasks) if dry_run_tasks else 0,
        }

    def create_task(
        self,
        title,
        description=None,
        tags=None,
        assignee=None,
        status=None,
        priority=None,
        subscribers=None,
        dry_run=False,
    ):
        """
        Create a single Maniphest task from CLI arguments.

        Parameters
        ----------
        title : str
            Task title (required)
        description : str, optional
            Task description
        tags : list, optional
            List of project names/tags (may contain plus-separated values)
        assignee : str, optional
            Username of the assignee
        status : str, optional
            Task status (Open, Resolved, etc.)
        priority : str, optional
            Task priority (Unbreak, Triage, High, Normal, Low, Wish)
        subscribers : list, optional
            List of subscriber usernames (may contain plus-separated values)
        dry_run : bool
            If True, validate and display without creating

        Returns
        -------
        dict or None
            Task info dict with 'phid', 'id', 'uri' keys, or None if dry_run

        Raises
        ------
        PhabfiveConfigException
            If validation fails (invalid priority, status, user not found, etc.)
        PhabfiveRemoteException
            If API call fails
        """
        # Parse plus-separated values (supports both repeat option and plus syntax)
        parsed_tags = self._parse_plus_separated(tags) if tags else []
        parsed_subscribers = (
            self._parse_plus_separated(subscribers) if subscribers else []
        )

        # Build transactions list
        transactions = []

        # Title is required
        transactions.append({"type": "title", "value": title})

        # Description is optional
        if description:
            transactions.append({"type": "description", "value": description})

        # Validate and resolve priority
        if priority:
            validated_priority = self._validate_priority(priority)
            transactions.append({"type": "priority", "value": validated_priority})

        # Validate and resolve status
        if status:
            validated_status = self._validate_status(status)
            transactions.append({"type": "status", "value": validated_status})

        # Resolve assignee username to PHID
        if assignee:
            assignee_phid = self._resolve_user_phid(assignee)
            if not assignee_phid:
                raise PhabfiveConfigException(
                    f"User '{assignee}' not found on Phabricator"
                )
            transactions.append({"type": "owner", "value": assignee_phid})

        # Resolve project tags to PHIDs and slugs
        project_slugs = []
        if parsed_tags:
            project_info = self._resolve_project_phids_for_create(parsed_tags)
            if project_info["phids"]:
                transactions.append(
                    {"type": "projects.set", "value": project_info["phids"]}
                )
                project_slugs = project_info["slugs"]

        # Resolve subscriber usernames to PHIDs
        if parsed_subscribers:
            subscriber_phids = self._resolve_user_phids(parsed_subscribers)
            if subscriber_phids:
                transactions.append(
                    {"type": "subscribers.set", "value": subscriber_phids}
                )

        # Dry run - return what would be created
        if dry_run:
            log.info("Dry run mode - task would be created with these transactions:")
            return {
                "dry_run": True,
                "title": title,
                "description": description,
                "priority": priority,
                "status": status,
                "assignee": assignee,
                "tags": parsed_tags,
                "subscribers": parsed_subscribers,
            }

        # Create the task via API
        try:
            result = self.phab.maniphest.edit(transactions=transactions)
            task_object = result["object"]

            # Fetch the task to get the URI
            task_id = task_object["id"]
            _, task_info = self.get_task_info(task_id)
            task_uri = task_info.get("uri", f"{self.url}T{task_id}")

            # Extract base URL from task URI for building tag URLs
            # e.g., "http://phorge.domain.tld/T5" -> "http://phorge.domain.tld"
            base_url = task_uri.rsplit("/", 1)[0] if "/" in task_uri else self.url

            return {
                "phid": task_object["phid"],
                "id": task_id,
                "uri": task_uri,
                "tag_slugs": project_slugs,
                "base_url": base_url,
            }
        except Exception as e:
            raise PhabfiveRemoteException(f"Failed to create task: {e}")
