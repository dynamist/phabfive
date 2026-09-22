# -*- coding: utf-8 -*-

"""Main Maniphest class that orchestrates all submodules."""

import copy
import itertools
import json
import logging
import functools
from pathlib import Path

from jinja2 import Template
from ruamel.yaml import YAML

from phabfive.constants import (
    MANIPHEST_ORDER_DEFAULT,
    MANIPHEST_ORDER_DIRECTIONS,
    MANIPHEST_ORDER_FIELDS,
    PRIORITY_DEFAULT,
    SEARCH_TEMPLATE_KEYS,
    STATUS_MAP_CACHE_NAMESPACE,
    TASK_POLICY_FIELDS,
    TASK_POLICY_TRANSACTIONS,
)
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveException,
    PhabfiveNotFoundException,
    PhabfiveRemoteException,
)
from phabfive.maniphest.fetchers import (
    fetch_all_transactions,
    fetch_project_names_for_boards,
    fetch_task_relationships,
    fallback_status_map,
    fetch_api_status_map,
    get_api_priority_map,
)
from phabfive.maniphest.filters import (
    task_matches_any_pattern,
    task_matches_priority_patterns,
    task_matches_project_patterns,
    task_matches_status_patterns,
)
from phabfive.maniphest.formatters import build_task_boards, build_task_display_data
from phabfive.maniphest.resolvers import (
    ambiguous_project_message,
    describe_space,
    describe_space_phid,
    fetch_all_spaces,
    fetch_project_lookup_maps,
    fetch_projects_by_phid,
    is_exact_monogram,
    resolve_project_phids,
    resolve_project_phids_for_create,
    resolve_space,
    resolve_space_phids,
)
from phabfive.maniphest.utils import (
    PHORGE_ORDER_KEYS,
    days_ago_to_timestamp,
    parse_time_with_unit,
    render_variables_with_dependency_resolution,
    sort_tasks,
)
from phabfive.ordering import parse_order
from phabfive.maniphest.validators import validate_priority, validate_status
from phabfive.me import is_me
from phabfive.options import split_list_option
from phabfive.policy import (
    policy_label,
    policy_lockout_message,
    resolve_policy_names,
    resolve_policy_value,
    validate_policy_value,
)
from phabfive.project_filters import parse_project_patterns
from phabfive.retry import idempotent_writes, is_idempotent_edit
from phabfive.users import resolve_user_phids as resolve_users

log = logging.getLogger(__name__)


def _once_per_instance(method):
    """Remember a no-argument method's answer on the instance that gave it.

    Replaces @lru_cache(maxsize=1), which kept its one entry in a cache
    shared by the class: it held the last instance alive, and two instances
    used in turn - two hosts, say - evicted each other on every call, each
    eviction costing the round trip again.
    """
    attribute = f"_once_{method.__name__}"

    @functools.wraps(method)
    def wrapper(self):
        try:
            return self.__dict__[attribute]
        except KeyError:
            value = self.__dict__[attribute] = method(self)
            return value

    return wrapper


class Maniphest(Phabfive):
    # Wrapper methods that delegate to submodules while maintaining self.phab access

    def _resolve_project_phids(self, project: str) -> list[str]:
        """Resolve project name, hashtag, or wildcard pattern to list of project PHIDs."""
        return resolve_project_phids(self.phab, project)

    def _get_open_statuses(self):
        """
        Get list of open status keys from the API.

        Returns
        -------
        list
            List of open status key strings (e.g., ["open"])
        """
        api_status = self._get_api_status_map()
        return api_status.get("openStatuses", ["open"])

    def _get_closed_statuses(self):
        """
        Get list of closed status keys from the API.

        maniphest.querystatuses lists them as a dict of index to key rather
        than as a list, unlike openStatuses.

        Returns
        -------
        list
            List of closed status key strings (e.g., ["resolved", "wontfix"])
        """
        closed = self._get_api_status_map().get("closedStatuses") or {}
        return list(closed.values()) if isinstance(closed, dict) else list(closed)

    def _validate_priority(self, priority):
        """Validate and normalize priority value."""
        return validate_priority(priority)

    def _validate_status(self, status):
        """Validate and normalize status value.

        A status the remembered map does not know may have been configured
        since it was remembered, so the server is asked once more before the
        status is refused - otherwise a new status could not be set until
        the entry expired. Only a real answer replaces the remembered map: a
        failed refetch keeps it and refuses the status, rather than judging
        this and every later status by the standard ones.
        """
        try:
            return validate_status(status, self._get_api_status_map())
        except PhabfiveConfigException as e:
            if not self.__dict__.pop("_status_map_remembered", False):
                raise
            refused = e

        try:
            status_map = fetch_api_status_map(self.phab)
        except Exception as e:
            log.warning(f"Failed to refetch statuses from API: {e}")
            raise refused from None

        self._remember_status_map(status_map)
        self.__dict__["_once__get_api_status_map"] = status_map
        return validate_status(status, status_map)

    def _resolve_users(self, values, option=None):
        """Resolve usernames, @usernames, @me and user PHIDs to (PHID, username)."""
        return resolve_users(self.phab, values, option=option)

    def _resolve_project_phids_for_create(self, project_names):
        """Resolve project names to PHIDs and slugs for task creation."""
        return resolve_project_phids_for_create(self.phab, project_names)

    def _fetch_project_names_for_boards(self, tasks_data):
        """Fetch project names for all boards in the task data."""
        return fetch_project_names_for_boards(self.phab, tasks_data)

    def _build_task_boards(self, boards, project_phid_to_name):
        """Build board information dict with current columns only."""
        return build_task_boards(boards, project_phid_to_name)

    @_once_per_instance
    def _get_api_priority_map(self):
        """Get mapping from Phabricator API numeric values to human-readable priority names."""
        return get_api_priority_map()

    @_once_per_instance
    def _get_api_status_map(self):
        """Get status information from Phabricator API.

        From the lookup store when the command gave the app one and it holds
        a map, which saves every command after the first the
        maniphest.querystatuses round trip.
        """
        if self.lookup_store is not None:
            remembered = self.lookup_store.get(STATUS_MAP_CACHE_NAMESPACE)
            if isinstance(remembered, dict) and remembered.get("statusMap"):
                self.__dict__["_status_map_remembered"] = True
                return remembered

        return self._fetch_status_map()

    def _fetch_status_map(self):
        """Ask the server for the status map, remembering only a real answer.

        A failed lookup is answered with the standard statuses, which a
        command needs to carry on with, but which are not written down: the
        store would hand them back for a week as though the server had said
        them.
        """
        try:
            status_map = fetch_api_status_map(self.phab)
        except Exception as e:
            log.warning(
                f"Failed to fetch statuses from API: {e}. Using fallback statuses."
            )
            return fallback_status_map()

        self._remember_status_map(status_map)
        return status_map

    def _remember_status_map(self, status_map):
        """Write a status map the server gave to the lookup store, if any."""
        if self.lookup_store is not None and status_map.get("statusMap"):
            self.lookup_store.set(STATUS_MAP_CACHE_NAMESPACE, status_map)

    @_once_per_instance
    def _get_all_spaces(self):
        """Every Space the viewer can see, probed once per command.

        A `--with` template runs a search per document, and each search can
        name several Spaces, so without this the whole probe runs again for
        every one of them.
        """
        return fetch_all_spaces(self.phab)

    def _resolve_space(self, space, all_spaces=None):
        """The one Space to place a task in, named by monogram, name or pattern."""
        # Enumerating every Space is only needed to match a wildcard or a
        # name; a monogram is resolved with a single direct lookup.
        if all_spaces is None and not is_exact_monogram(space):
            all_spaces = self._get_all_spaces()

        return resolve_space(self.phab, space, all_spaces=all_spaces)

    def _resolve_space_patterns(self, space_patterns):
        """Resolve comma-separated Space patterns to PHIDs, in order."""
        # Enumerating every Space is only needed to match a wildcard or a
        # name; a monogram is resolved with a single direct lookup.
        all_spaces = None
        if any(not is_exact_monogram(pattern) for pattern in space_patterns):
            all_spaces = self._get_all_spaces()

        space_phids = []
        for space_pattern in space_patterns:
            space_phids.extend(
                resolve_space_phids(self.phab, space_pattern, all_spaces=all_spaces)
            )

        # Remove duplicates while preserving order
        return list(dict.fromkeys(space_phids))

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
        parents_map=None,
        subtasks_map=None,
        matching_boards_map=None,
        matching_priority_map=None,
        matching_status_map=None,
        show_history=False,
        show_metadata=False,
        show_comments=False,
        show_policy=False,
        search_params=None,
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
            parents_map=parents_map,
            subtasks_map=subtasks_map,
            matching_boards_map=matching_boards_map,
            matching_priority_map=matching_priority_map,
            matching_status_map=matching_status_map,
            show_history=show_history,
            show_metadata=show_metadata,
            show_comments=show_comments,
            show_policy=show_policy,
            search_params=search_params,
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
        from phabfive.transitions import parse_status_patterns

        api_response = self._get_api_status_map()
        return parse_status_patterns(patterns_str, api_response)

    def task_show(
        self,
        task_ids,
        show_history=False,
        show_metadata=False,
        show_comments=False,
        show_policy=False,
        show_description=True,
    ):
        """
        Show one or more Phabricator Maniphest tasks with optional history and metadata.

        This method uses the same display format as task_search() for consistency.

        Parameters
        ----------
        task_ids : list[int]
            Task IDs (e.g., [123, 456] for T123, T456)
        show_history : bool, optional
            If True, display column, priority, and status transition history
        show_metadata : bool, optional
            If True, display metadata (mainly useful for debugging, less useful for single task)
        show_comments : bool, optional
            If True, display comments on the task
        show_policy : bool, optional
            If True, display the task's policies. Naming them costs a
            ``phid.query``, so it is not paid for unless it was asked for.
        show_description : bool, optional
            If True, include task description in output. Default is True.
        """
        # Use maniphest.search API to fetch all tasks in one call
        result = self.phab.maniphest.search(
            constraints={"ids": task_ids}, attachments={"columns": True}
        )

        result_data = result.response.get("data", [])

        if not result_data:
            for task_id in task_ids:
                log.error(f"Task T{task_id} not found")
            return None

        # Report any tasks that were not found
        found_ids = {t["id"] for t in result_data}
        missing_ids = [tid for tid in task_ids if tid not in found_ids]
        for task_id in missing_ids:
            log.error(f"Task T{task_id} not found")

        # The API returns tasks in its own order (newest first); reorder to
        # match the order the tasks were requested in
        requested_order = {tid: i for i, tid in enumerate(task_ids)}
        result_data.sort(
            key=lambda t: requested_order.get(t["id"], len(requested_order))
        )

        # Initialize maps for storing transitions, assignee, and comments history
        task_transitions_map = {}
        priority_transitions_map = {}
        status_transitions_map = {}
        assignee_transitions_map = {}
        comments_map = {}
        parents_map = {}
        subtasks_map = {}

        for task_data in result_data:
            task_id = task_data["id"]
            task_phid = task_data.get("phid")

            # Fetch transaction data if any transaction-based info is requested
            if (show_history or show_comments) and task_phid:
                log.debug(f"Fetching transactions for T{task_id}")
                all_fetched_transactions = self._fetch_all_transactions(
                    task_phid,
                    need_columns=show_history,
                    need_priority=show_history,
                    need_status=show_history,
                    need_assignee=show_history,
                    need_comments=show_comments,
                )
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

            # Fetch parent and subtask relationships
            if task_phid:
                parent_phids = fetch_task_relationships(self.phab, task_phid, "parents")
                subtask_phids = fetch_task_relationships(
                    self.phab, task_phid, "subtasks"
                )

                # Resolve PHIDs to task IDs and titles
                all_related_phids = parent_phids + subtask_phids
                if all_related_phids:
                    related_result = self.phab.maniphest.search(
                        constraints={"phids": all_related_phids}
                    )
                    phid_to_info = {
                        t["phid"]: {
                            "id": t["id"],
                            "name": t["fields"].get("name", ""),
                        }
                        for t in related_result.get("data", [])
                    }

                    parents_map[task_id] = [
                        {
                            "Link": f"{self.url}/T{phid_to_info[p]['id']}",
                            "Task": {"Name": phid_to_info[p]["name"]},
                        }
                        for p in parent_phids
                        if p in phid_to_info
                    ]
                    subtasks_map[task_id] = [
                        {
                            "Link": f"{self.url}/T{phid_to_info[p]['id']}",
                            "Task": {"Name": phid_to_info[p]["name"]},
                        }
                        for p in subtask_phids
                        if p in phid_to_info
                    ]
                else:
                    parents_map[task_id] = []
                    subtasks_map[task_id] = []

        # Use shared method to build task data
        display_data = self._build_task_display_data(
            result_data,
            task_transitions_map=task_transitions_map,
            priority_transitions_map=priority_transitions_map,
            status_transitions_map=status_transitions_map,
            assignee_transitions_map=assignee_transitions_map,
            comments_map=comments_map,
            parents_map=parents_map,
            subtasks_map=subtasks_map,
            show_history=show_history,
            show_metadata=show_metadata,
            show_comments=show_comments,
            show_policy=show_policy,
        )

        # Let the caller tell a partial result from a complete one, so asking
        # for a task that does not exist can be reported as a failure
        display_data["missing_ids"] = missing_ids

        return display_data

    def get_related_tasks(self, task_id, relationship_type):
        """
        Get parent or subtask tasks for a given task.

        Parameters
        ----------
        task_id : int
            Task ID (e.g., 123 for T123)
        relationship_type : str
            Either "parents" or "subtasks"

        Returns
        -------
        dict
            Dictionary with 'tasks' key containing list of task display data,
            or None if source task not found
        """
        # First get the source task's PHID
        result = self.phab.maniphest.search(constraints={"ids": [task_id]})
        result_data = result.response.get("data", [])

        if not result_data:
            log.error(f"Task T{task_id} not found")
            return None

        task_phid = result_data[0].get("phid")

        # Fetch related task PHIDs using edge.search
        related_phids = fetch_task_relationships(
            self.phab, task_phid, relationship_type
        )

        if not related_phids:
            log.debug(f"No {relationship_type} found for T{task_id}")
            return {"tasks": []}

        # Fetch full task data for related tasks
        related_result = self.phab.maniphest.search(
            constraints={"phids": related_phids}, attachments={"columns": True}
        )

        related_data = related_result.response.get("data", [])

        if not related_data:
            return {"tasks": []}

        # Fetch parents/subtasks for each related task
        parents_map = {}
        subtasks_map = {}
        for task in related_data:
            task_id = task["id"]
            task_phid = task["phid"]

            parent_phids = fetch_task_relationships(self.phab, task_phid, "parents")
            subtask_phids = fetch_task_relationships(self.phab, task_phid, "subtasks")

            # Resolve PHIDs to task IDs and titles
            all_rel_phids = parent_phids + subtask_phids
            if all_rel_phids:
                rel_result = self.phab.maniphest.search(
                    constraints={"phids": all_rel_phids}
                )
                phid_to_info = {
                    t["phid"]: {"id": t["id"], "name": t["fields"].get("name", "")}
                    for t in rel_result.get("data", [])
                }

                parents_map[task_id] = [
                    {
                        "Link": f"{self.url}/T{phid_to_info[p]['id']}",
                        "Task": {"Name": phid_to_info[p]["name"]},
                    }
                    for p in parent_phids
                    if p in phid_to_info
                ]
                subtasks_map[task_id] = [
                    {
                        "Link": f"{self.url}/T{phid_to_info[p]['id']}",
                        "Task": {"Name": phid_to_info[p]["name"]},
                    }
                    for p in subtask_phids
                    if p in phid_to_info
                ]
            else:
                parents_map[task_id] = []
                subtasks_map[task_id] = []

        # Build display data for related tasks
        return self._build_task_display_data(
            related_data,
            parents_map=parents_map,
            subtasks_map=subtasks_map,
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
            raise PhabfiveConfigException(f"Template file not found: {template_path}")

        if not template_file.is_file():
            raise PhabfiveConfigException(f"Path is not a file: {template_path}")

        try:
            with open(template_file, "r", encoding="utf-8") as f:
                yaml_loader = YAML()
                # Load all documents from the YAML file
                documents = list(yaml_loader.load_all(f))
        except Exception as e:
            raise PhabfiveDataException(
                f"Failed to parse template file {template_path}: {e}"
            )

        if not documents:
            raise PhabfiveDataException("Template file contains no documents")

        search_configs = []
        supported_params = SEARCH_TEMPLATE_KEYS

        for i, data in enumerate(documents):
            if not isinstance(data, dict):
                raise PhabfiveDataException(
                    f"Document {i + 1} in YAML file must contain a dictionary at root level"
                )

            search_params = data.get("search", {})
            if not isinstance(search_params, dict):
                raise PhabfiveDataException(
                    f"Document {i + 1}: 'search' section must be a dictionary"
                )

            # Validate supported parameters
            invalid_params = set(search_params.keys()) - supported_params
            if invalid_params:
                raise PhabfiveDataException(
                    f"Document {i + 1}: Unsupported search parameters: {', '.join(invalid_params)}. "
                    f"Supported: {', '.join(sorted(supported_params))}"
                )

            # Keep an omitted title distinct from a generated display label.
            # The CLI uses this to decide whether a single template was
            # explicitly named by its author.
            config = {
                "search": search_params,
                "title": data.get("title"),
                "description": data.get("description", None),
            }
            search_configs.append(config)

        log.info(
            f"Loaded {len(search_configs)} search configuration(s) from {template_path}"
        )
        for i, config in enumerate(search_configs):
            log.debug(f"Search {i + 1} parameters: {config['search']}")

        return search_configs

    def _resolve_user_filter_phids(self, value, label, option=None):
        """
        Resolve a user search filter into PHIDs.

        Accepts "@me", a username or @username, a user PHID, or a
        comma-separated list of them for OR logic, e.g. "@me,user1,@user2".

        Parameters
        ----------
        value : str or None
            The raw filter value. Falsy values mean "no filter".
        label : str
            Used in the log line, e.g. "assigned to" or "authored by".
        option : str, optional
            The option the value came from, named in any error about @me

        Returns
        -------
        list
            User PHIDs, in the order given. Empty if there is no filter.

        Raises
        ------
        PhabfiveDataException
            If any name cannot be resolved to a PHID. Filtering on a name
            that does not exist would otherwise look like "no matches".
        """
        if not value:
            return []

        users = self._resolve_users(
            [n.strip() for n in value.split(",") if n.strip()], option=option
        )
        phids = list(dict.fromkeys(phid for phid, _ in users.values()))
        resolved_names = [
            f"@me ({username})" if is_me(name) else (username or name)
            for name, (_, username) in users.items()
        ]

        if len(resolved_names) > 1:
            log.info(f"Filtering by tasks {label} any of: {', '.join(resolved_names)}")
        else:
            log.info(f"Filtering by tasks {label} {resolved_names[0]}")

        return phids

    def _build_search_constraints(
        self,
        status_scope="open",
        text_query=None,
        assigned_phids=None,
        author_phids=None,
        space_phids=None,
        created_after=None,
        created_before=None,
        updated_after=None,
        updated_before=None,
    ):
        """
        Build the shared constraints for a maniphest.search call.

        Every task_search code path applies the same filters; only the
        project selection differs, so callers add "projects" themselves.

        Returns
        -------
        dict
            Constraints accepted by maniphest.search. Note that the names
            are specific to this endpoint, see AGENTS.md.
        """
        constraints = {}

        if status_scope == "open":
            open_statuses = self._get_open_statuses()
            constraints["statuses"] = open_statuses
            log.info(f"Filtering to open statuses: {open_statuses}")
        elif status_scope == "closed":
            closed_statuses = self._get_closed_statuses()
            constraints["statuses"] = closed_statuses
            log.info(f"Filtering to closed statuses: {closed_statuses}")

        if text_query:
            log.info(f"Free-text search: '{text_query}'")
            # Note: maniphest.search doesn't have a fullText constraint
            # We use the 'query' constraint which searches titles and descriptions
            constraints["query"] = text_query

        if assigned_phids:
            constraints["assigned"] = assigned_phids

        if author_phids:
            # maniphest.search names this "authorPHIDs"; paste.search calls
            # its own author filter "authors". See AGENTS.md.
            constraints["authorPHIDs"] = author_phids

        if space_phids:
            constraints["spaces"] = space_phids

        if created_after:
            constraints["createdStart"] = int(created_after)
        if created_before:
            constraints["createdEnd"] = int(created_before)
        if updated_after:
            constraints["modifiedStart"] = int(updated_after)
        if updated_before:
            constraints["modifiedEnd"] = int(updated_before)

        return constraints

    def _search_all_pages(self, constraints, log_context="", order=None):
        """
        Run maniphest.search, following cursors until every page is read.

        The API returns at most 100 tasks per page.

        Parameters
        ----------
        constraints : dict
            Constraints for the search.
        log_context : str
            Optional prefix for the per-page debug line, e.g. a project PHID.
        order : str, optional
            A builtin maniphest.search order name. Cursor paging respects it,
            so the pages stay in order as they are concatenated.

        Returns
        -------
        list
            Task dicts from every page, in the order returned.
        """
        tasks = []
        after = None

        while True:
            kwargs = {"constraints": constraints, "attachments": {"columns": True}}
            if order:
                kwargs["order"] = order
            if after:
                kwargs["after"] = after

            result = self.phab.maniphest.search(**kwargs)
            page = result.response["data"]
            tasks.extend(page)

            cursor = result.get("cursor", {})
            after = cursor.get("after")
            log.debug(
                f"{log_context}fetched page with {len(page)} tasks, "
                f"total so far: {len(tasks)}, next cursor: {after}"
            )

            if after is None:
                # No more pages
                break

        return tasks

    def task_search(
        self,
        text_query=None,
        tag=None,
        include_task_ids=None,
        exclude_task_ids=None,
        assigned=None,
        author=None,
        space=None,
        created_after=None,
        created_before=None,
        updated_after=None,
        updated_before=None,
        visible_to=None,
        editable_by=None,
        column_patterns=None,
        priority_patterns=None,
        status_patterns=None,
        show_history=False,
        show_metadata=False,
        show_policy=False,
        include_closed=False,
        limit=100,
        order=None,
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
        include_task_ids (list[int], optional): Task IDs to force-include in the results.
                      Bypasses all filters, post-filters, and the limit; deduplicated
                      against matched results. With no other filters, returns exactly
                      these tasks without running a general search.
        exclude_task_ids (list[int], optional): Task IDs to remove from the results even
                      when the filters match them. Applied before the limit, so freed
                      slots fill with other matches. Non-matching IDs are ignored.
        assigned      (str, optional): Filter by assignee. Use "@me" to filter tasks assigned to you,
                      or provide username(s). Comma-separated for OR logic (e.g., "@me,user1,user2").
        author        (str, optional): Filter by task author. Use "@me" to filter tasks you created,
                      or provide username(s). Comma-separated for OR logic (e.g., "@me,user1,user2").
        space         (str, optional): Space name or monogram (e.g., "S1" or "Public") to filter tasks by.
                      Limits search results to tasks in the specified Space.
        created_after (str|int, optional): Time period for task creation (e.g., "7d", "2w", "1m") or days as int.
                      Supports units: h (hours), d (days), w (weeks), m (months), y (years).
        created_before (str|int, optional): Tasks created more than TIME ago (e.g., "7d", "2w", "1m") or days as int.
                      Supports units: h (hours), d (days), w (weeks), m (months), y (years).
        updated_after (str|int, optional): Time period for task updates (e.g., "7d", "2w", "1m") or days as int.
                      Supports units: h (hours), d (days), w (weeks), m (months), y (years).
        updated_before (str|int, optional): Tasks updated more than TIME ago (e.g., "7d", "2w", "1m") or days as int.
                      Supports units: h (hours), d (days), w (weeks), m (months), y (years).
        visible_to    (str, optional): Only tasks whose view policy is exactly this, in the
                      grammar phabfive.policy accepts (public, users, admin, no-one,
                      #project, @user, @me or a PHID). Compared with the stored value, so
                      "users" finds tasks set to All Users - not every task a user can
                      see. maniphest.search has no policy constraint, so this runs on
                      the client, before --limit.
        editable_by   (str, optional): Only tasks whose edit policy is exactly this; as
                      visible_to.
        column_patterns (list, optional): List of ColumnPattern objects to filter by.
                      Filters tasks based on column transitions (from, to, in, been, never, forward, backward).
        priority_patterns (list, optional): List of PriorityPattern objects to filter by.
                      Filters tasks based on priority transitions (from, to, in, been, never, raised, lowered).
        status_patterns (list, optional): List of StatusPattern objects to filter by.
                      Filters tasks based on status transitions (from, to, in, been, never, raised, lowered).
                      The scope keywords open, closed and any decide which statuses are fetched
                      at all; a group naming none reaches open tasks only. See
                      phabfive.transitions.status.resolve_status_scope.
        show_history (bool, optional): If True, display column, priority, and status transition history for each task.
                      Must be explicitly requested; not auto-enabled by filters.
        show_metadata (bool, optional): If True, display which boards/priorities/statuses matched the filters.
        show_policy   (bool, optional): If True, display each task's policies. Naming them costs a
                      phid.query for the whole page, which a search that was not asked for a policy
                      does not pay.
                      Shows MatchedBoards list, MatchedPriority, and MatchedStatus boolean for debugging filter logic.
        include_closed (bool, optional): Deprecated, as --all is: the same as a status
                      scope of "any" for every --status group that names no scope of its own.
        limit         (int, optional): Maximum number of tasks to return. Default is 100.
                      Applied after ordering, so it keeps the top N of the requested order.
        order         (str, optional): Result ordering as "<field>[:asc|:desc]", e.g.
                      "updated:asc". Defaults to "priority", matching Phorge's own default.
        """
        # Validation - require at least one filter. A scope keyword alone
        # (--status=any) counts, as a status pattern always has, and so does
        # include_closed, the deprecated spelling of the same thing (#419).
        has_other_filters = any(
            [
                include_closed,
                text_query,
                tag,
                assigned,
                author,
                space,
                created_after,
                created_before,
                updated_after,
                updated_before,
                visible_to,
                editable_by,
                column_patterns,
                priority_patterns,
                status_patterns,
            ]
        )

        if not has_other_filters and not include_task_ids:
            raise PhabfiveConfigException("No search criteria specified")

        # Which statuses the server is asked for. Groups made only of scope
        # keywords are answered by the server, so --status=any costs no
        # history fetch; a transition pattern keeps the open default.
        from phabfive.transitions.status import (
            resolve_status_scope,
            unreachable_conditions,
        )

        default_scope = "any" if include_closed else "open"
        status_map = self._get_api_status_map() if status_patterns else None
        for condition, scope in unreachable_conditions(
            status_patterns, default_scope, status_map
        ):
            log.warning(
                f"--status {condition} cannot match: the search reaches {scope} "
                f"tasks only. Add a scope, e.g. --status='any+{condition}'"
            )
        status_scope, status_patterns = resolve_status_scope(
            status_patterns, default_scope
        )

        # Resolved once, before anything is fetched, so a typo or a project
        # that does not exist fails fast rather than after the whole search.
        policy_filter = {
            TASK_POLICY_FIELDS[key]: resolve_policy_value(
                self.phab, value, option=option
            )
            for key, value, option in (
                ("view", visible_to, "--visible-to"),
                ("edit", editable_by, "--editable-by"),
            )
            if value is not None
        }

        # Resolved before anything is fetched so a bad --order fails fast, and
        # so library callers get the same validation the CLI does.
        order_field, order_direction = parse_order(
            order,
            MANIPHEST_ORDER_FIELDS,
            MANIPHEST_ORDER_DIRECTIONS,
            MANIPHEST_ORDER_DEFAULT,
        )
        api_order = PHORGE_ORDER_KEYS.get((order_field, order_direction))
        log.info(f"Ordering results by '{order_field}:{order_direction}'")

        # Convert date filters to Unix timestamps (preserve original values for logging)
        created_after_original = created_after
        created_before_original = created_before
        updated_after_original = updated_after
        updated_before_original = updated_before

        if created_after:
            created_after_days = parse_time_with_unit(created_after)
            created_after = days_ago_to_timestamp(created_after_days)
        if created_before:
            created_before_days = parse_time_with_unit(created_before)
            created_before = days_ago_to_timestamp(created_before_days)
        if updated_after:
            updated_after_days = parse_time_with_unit(updated_after)
            updated_after = days_ago_to_timestamp(updated_after_days)
        if updated_before:
            updated_before_days = parse_time_with_unit(updated_before)
            updated_before = days_ago_to_timestamp(updated_before_days)

        # Resolve the user filters - convert @me or username(s) to PHID(s)
        assigned_phids = self._resolve_user_filter_phids(
            assigned, "assigned to", option="--assigned"
        )
        author_phids = self._resolve_user_filter_phids(
            author, "authored by", option="--author"
        )

        # Resolve space filter - convert space name/monogram(s) to PHID(s)
        # Supports: glob patterns (*, S*, *Public*), comma-separated list (S9,S10)
        # Default (space=None): filter to S1 (Global) space only
        space_phids = []

        if space:
            # Split by comma to support multiple spaces (OR logic)
            space_patterns = [s.strip() for s in space.split(",")]

            space_phids = self._resolve_space_patterns(space_patterns)

            log.info(f"Filtering to space(s): {space}")
        else:
            # Default to configured space(s) - supports glob patterns and comma-separated
            default_space = self.conf.get("PHAB_SPACE", "S1")
            try:
                # Split by comma to support multiple default spaces
                space_patterns = [s.strip() for s in default_space.split(",")]

                space_phids = self._resolve_space_patterns(space_patterns)

                log.info(
                    f"Filtering to space(s): {default_space} (from PHAB_SPACE). "
                    "Tasks in other spaces are excluded; "
                    "use --space='*' to include all spaces."
                )
            except Exception as e:
                log.warning(
                    f"Could not resolve default space '{default_space}': {e}. "
                    "Showing all spaces."
                )
                space_phids = []

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

                    # Sorted, not just listed: set iteration order over strings
                    # is randomised per process (PYTHONHASHSEED), which made the
                    # per-project merge below come out differently on every run.
                    project_phids = sorted(resolved_phids_set)

                    if not project_phids:
                        log.error(f"No projects matched the tag pattern '{tag}'")
                        return

                    # Determine AND vs OR logic for logging
                    has_and_patterns = any(
                        len(p.project_names) > 1 for p in project_patterns
                    )
                    logic_type = "AND" if has_and_patterns else "OR"
                    log.info(
                        f"Filtering to tag(s): {tag} "
                        f"({len(project_phids)} project(s), {logic_type} logic)"
                    )
                except PhabfiveException as e:
                    log.error(f"Invalid tag pattern: {e}")
                    return
            else:
                project_phids = self._resolve_project_phids(tag)
                if not project_phids:
                    # Error already logged in _resolve_project_phids
                    return

                log.info(
                    f"Filtering to tag(s): {tag} ({len(project_phids)} project(s))"
                )

        if include_task_ids and not has_other_filters:
            # --include is the only criterion: skip the general search entirely
            # (an empty constraints dict would fetch every open task); the
            # included tasks are fetched and merged below.
            log.info("Only --include specified, skipping general search")
            result_data = []
        elif tag == "*" or tag is None:
            if tag == "*":
                log.info("Searching across all projects (tag='*', no project filter)")
            else:
                log.info("No tag specified, searching across all projects")
            constraints = self._build_search_constraints(
                status_scope=status_scope,
                text_query=text_query,
                assigned_phids=assigned_phids,
                author_phids=author_phids,
                space_phids=space_phids,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                updated_before=updated_before,
            )

            result_data = self._search_all_pages(constraints, order=api_order)
        else:
            base_constraints = self._build_search_constraints(
                status_scope=status_scope,
                text_query=text_query,
                assigned_phids=assigned_phids,
                author_phids=author_phids,
                space_phids=space_phids,
                created_after=created_after,
                created_before=created_before,
                updated_after=updated_after,
                updated_before=updated_before,
            )

            # Handle multiple projects (make separate calls and merge)
            if len(project_phids) > 1:
                all_tasks = {}  # task_id -> task_data
                for phid in project_phids:
                    constraints = {**base_constraints, "projects": [phid]}

                    # Merge each project's tasks, avoiding duplicates
                    for item in self._search_all_pages(
                        constraints,
                        log_context=f"Project {phid}: ",
                        order=api_order,
                    ):
                        all_tasks.setdefault(item["id"], item)

                # Convert back to list for display
                result_data = list(all_tasks.values())
            else:
                # Single project
                constraints = {**base_constraints, "projects": project_phids}

                result_data = self._search_all_pages(constraints, order=api_order)

        # Order the merged result before anything downstream narrows it. The
        # post-filters and --exclude below preserve order, so the --limit
        # truncation keeps the top N of the requested order rather than an
        # arbitrary slice of a project-grouped merge.
        result_data = sort_tasks(result_data, order_field, order_direction)

        # Policy filters are exact matches on the stored value. Applied here,
        # before the transition filters fetch any history and before --limit
        # truncates, so the limit keeps the top N of the tasks that match.
        if policy_filter:
            matched = [
                task
                for task in result_data
                if all(
                    ((task.get("fields") or {}).get("policy") or {}).get(field)
                    == wanted
                    for field, wanted in policy_filter.items()
                )
            ]
            log.info(f"Policy filter kept {len(matched)} of {len(result_data)} tasks")
            result_data = matched

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
        need_columns = bool(column_patterns) or show_history
        need_priority = bool(priority_patterns) or show_history
        need_status = bool(status_patterns) or show_history

        # Apply transition filtering if patterns specified
        if column_patterns or priority_patterns or status_patterns or project_patterns:
            filter_desc = []
            if text_query:
                filter_desc.append(f"query='{text_query}'")
            if tag:
                filter_desc.append(f"tag='{tag}'")
            if created_after:
                filter_desc.append(f"created-after={created_after_original}")
            if created_before:
                filter_desc.append(f"created-before={created_before_original}")
            if updated_after:
                filter_desc.append(f"updated-after={updated_after_original}")
            if updated_before:
                filter_desc.append(f"updated-before={updated_before_original}")
            if column_patterns:
                col_strs = [str(p) for p in column_patterns]
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

                if column_patterns:
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
                            column_patterns,
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

        # Build search params for metadata embedding
        search_params = None
        if show_metadata:
            search_params = {}
            if tag:
                search_params["tag"] = tag
            if text_query:
                search_params["text_query"] = text_query
            if assigned:
                search_params["assigned"] = assigned
            if author:
                search_params["author"] = author
            if space:
                search_params["space"] = space
            if visible_to:
                search_params["visible_to"] = visible_to
            if editable_by:
                search_params["editable_by"] = editable_by
            if updated_after_original:
                search_params["updated_after"] = updated_after_original
            if updated_before_original:
                search_params["updated_before"] = updated_before_original
            if created_after_original:
                search_params["created_after"] = created_after_original
            if created_before_original:
                search_params["created_before"] = created_before_original
            if include_task_ids:
                search_params["include"] = ",".join(
                    f"T{tid}" for tid in include_task_ids
                )
            if exclude_task_ids:
                search_params["exclude"] = ",".join(
                    f"T{tid}" for tid in exclude_task_ids
                )

        # Remove excluded tasks before the limit so freed slots fill with
        # other matches; IDs that didn't match anything are ignored.
        if exclude_task_ids:
            excluded = set(exclude_task_ids)
            result_data = [t for t in result_data if t["id"] not in excluded]

        # Apply limit if specified
        if limit and len(result_data) > limit:
            result_data = result_data[:limit]

        # Force-include tasks from --include: merged after post-filtering and
        # after the limit so they always survive; deduplicated by task id.
        if include_task_ids:
            included_result = self.phab.maniphest.search(
                constraints={"ids": list(include_task_ids)},
                attachments={"columns": True},
            )
            included_tasks = included_result.response.get("data", [])

            found_ids = {t["id"] for t in included_tasks}
            for tid in include_task_ids:
                if tid not in found_ids:
                    log.error(f"Task T{tid} not found")

            # The API returns tasks in its own order (newest first); append
            # in the order the tasks were given to --include
            requested_order = {tid: i for i, tid in enumerate(include_task_ids)}
            included_tasks.sort(
                key=lambda t: requested_order.get(t["id"], len(requested_order))
            )

            existing_ids = {t["id"] for t in result_data}
            for item in included_tasks:
                if item["id"] in existing_ids:
                    # Already matched the search; keep the single copy
                    continue
                result_data.append(item)
                if show_history and item.get("phid"):
                    all_fetched_transactions = self._fetch_all_transactions(
                        item["phid"],
                        need_columns=True,
                        need_priority=True,
                        need_status=True,
                    )
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
            show_policy=show_policy,
            search_params=search_params,
        )

    def add_task_comment(self, ticket_identifier, comment_string):
        """Comment on a task.

        Parameters
        ----------
        ticket_identifier : str
            The task, e.g. "T123"
        comment_string : str

        Returns
        -------
        dict
            The edited object, with its "id" and "phid"
        """
        result = self.phab.maniphest.edit(
            transactions=self.to_transactions({"comment": comment_string}),
            objectIdentifier=ticket_identifier,
        )

        return result["object"]

    def get_task_info(self, task_id):
        """Return maniphest.info's answer for a task: its uri, status and so on.

        Parameters
        ----------
        task_id : int
        """
        # FIXME: Add validation and extraction of the int part of the task_id
        return self.phab.maniphest.info(task_id=task_id)

    def create_tasks_from_yaml(self, create_config, dry_run=False):
        """Create the tasks a creation template file describes.

        Reads the file and hands it to create_tasks_from_config; see there.
        """
        if not create_config:
            raise PhabfiveConfigException("Must specify a config file path")

        if not Path(create_config).is_file():
            raise PhabfiveConfigException(
                f"Config file '{create_config}' does not exist"
            )

        # A file that is not one YAML document - a second `---` document, a
        # tab where a space belongs - used to hand ruamel's own error to the
        # user as a traceback: the command answers only the phabfive
        # exceptions, so nothing caught it (#466)
        try:
            with open(create_config) as stream:
                yaml_loader = YAML()
                root_data = yaml_loader.load(stream)
        except Exception as e:
            raise PhabfiveDataException(
                f"Failed to parse template file {create_config}: {e}"
            )

        return self.create_tasks_from_config(root_data, dry_run=dry_run)

    def create_tasks_from_config(self, config, dry_run=False):
        """Create the tasks a creation template describes, from its data.

        For a program that holds the template as data rather than as a file:
        `config` is what the template file would parse to, with its
        `variables` and `tasks` (see docs/create-templates.md). It is read,
        never written: the copy this works on is what loses its `variables`
        and carries the normalized priorities.

        Returns
        -------
        dict
            With "dry_run": True and the "tasks" that would be created, or
            the "task_ids" that were.
        """
        if not isinstance(config, dict):
            raise PhabfiveDataException(
                "A creation template is a mapping at the root level, not "
                f"{type(config).__name__}"
            )

        # A program holding the template as data may hand the same dict to
        # this twice, so neither the `variables` pop below nor the priority
        # normalization may reach into what the caller still holds
        root_data = copy.deepcopy(config)

        if dry_run:
            log.warning("DRY RUN: Tasks will not be created in Phabricator")

        def validate_priorities(task_config):
            """Validate and normalize the priority of every task in the tree.

            The same check --priority gets, and before anything is fetched or
            created: an unvalidated priority reaches the server verbatim, and
            a typo comes back as an opaque Conduit error instead of naming
            the valid priorities (#465).
            """
            priority = task_config.get("priority")

            if priority is not None:
                # A `-` in YAML is null, and 2 is a number; neither names a
                # priority, and neither survives validate_priority's .lower()
                if not isinstance(priority, str):
                    raise PhabfiveConfigException(
                        f"priority takes a priority name, not {priority!r}"
                    )

                # Normalized in place, so the transaction built later carries
                # the API's spelling of it rather than the template's
                task_config["priority"] = self._validate_priority(priority)

            for child_task in task_config.get("tasks") or []:
                validate_priorities(child_task)

        validate_priorities(root_data)

        # Fetch all projects in phabricator, used to map ticket -> projects later.
        # The map keys lowercased primary names AND slugs/hashtags so that
        # project references in YAML match case-insensitively,
        # mirroring resolve_project_phids_for_create() used by the --tag path.
        project_name_to_id_map, _, ambiguous_project_names = fetch_project_lookup_maps(
            self.phab
        )

        log.debug(project_name_to_id_map)

        # Gather and remove variables to avoid using it or polluting the data
        # later on. The key is optional, and an empty or null one is a template
        # that defines no variables rather than an error
        variables = root_data.pop("variables", None) or {}

        # A list or a scalar has no names to render with, and asking it for
        # .items() is the traceback the command has no handler for
        if not isinstance(variables, dict):
            raise PhabfiveConfigException(
                f"variables takes a mapping of name to value, not {variables!r}"
            )

        # Render variables that reference other variables using dependency resolution
        variables = render_variables_with_dependency_resolution(variables)

        # Main task recursion logic
        if "tasks" not in root_data:
            raise PhabfiveDataException(
                "Config file must contain keyword tasks in the root"
            )

        def render(value):
            return Template(value).render(variables) if value else value

        def template_users(task_config):
            """What every task in the tree names for assignment and subscribers."""
            assignment = task_config.get("assignment")

            if assignment is not None and not isinstance(assignment, str):
                raise PhabfiveConfigException(
                    f"assignment takes one user, not {assignment!r}"
                )

            subscribers = task_config.get("subscribers") or []

            # Iterating a string would ask for each letter as a user, and a
            # mapping for each of its keys
            if not isinstance(subscribers, list):
                raise PhabfiveConfigException(
                    f"subscribers takes a list of users, not {subscribers!r}"
                )

            # A stray `-` in YAML is a null item, and 1234 is a number
            not_names = [name for name in subscribers if not isinstance(name, str)]

            if not_names:
                raise PhabfiveConfigException(
                    f"subscribers takes usernames, not {not_names[0]!r}"
                )

            assignments = [render(assignment)] if assignment else []
            subscribed = [render(name) for name in subscribers]

            for child_task in task_config.get("tasks") or []:
                child_assignments, child_subscribed = template_users(child_task)
                assignments += child_assignments
                subscribed += child_subscribed

            return assignments, subscribed

        # Every user the template names, looked up once for the whole tree
        # and before any task is created, so that a typo in the tenth task
        # does not leave nine behind. Only those users are asked about: a
        # single unpaged user.search saw the first 100 users and no more.
        assignments, subscribed = template_users(root_data)
        users = {}

        for values, option in (
            (assignments, "assignment"),
            (subscribed, "subscribers"),
        ):
            if values:
                users.update(
                    self._resolve_users(list(dict.fromkeys(values)), option=option)
                )

        # The username each PHID was resolved to, for the dry-run preview
        user_names = {phid: username or phid for phid, username in users.values()}

        # A template can put every task in the same Space, so each one named
        # is resolved once rather than once per task naming it
        resolved_spaces = {}

        def space_phid_for(space_name):
            if space_name not in resolved_spaces:
                resolved_spaces[space_name] = self._resolve_space(space_name)["phid"]

            return resolved_spaces[space_name]

        # Helper function to slim down transaction handling
        def add_transaction(t, transaction_type, value):
            t.append({"type": transaction_type, "value": value})

        def r(data_block, variable_name, variables):
            """
            Helper method to simplify Jinja2 rendering of a given value to a set of variables
            """
            data = data_block.get(variable_name, None)

            if data:
                data_block[variable_name] = render(data)

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

            project_names = output.get("projects") or []

            # Iterating a string would ask for a project per letter, and a
            # mapping for each of its keys, the way subscribers refuses
            if not isinstance(project_names, list):
                raise PhabfiveConfigException(
                    f"projects takes a list of project names, not {project_names!r}"
                )

            for project_name in project_names:
                # A `-` in YAML is a null item, and 1234 is a number; neither
                # renders, and neither names a project
                if not isinstance(project_name, str):
                    raise PhabfiveConfigException(
                        f"projects takes project names, not {project_name!r}"
                    )

                # Rendered like every other string field, and before the name
                # is looked up: a template names a project by variable too
                project_name = render(project_name)

                ambiguous_phids = ambiguous_project_names.get(project_name.lower())
                if ambiguous_phids:
                    raise PhabfiveConfigException(
                        ambiguous_project_message(
                            project_name,
                            fetch_projects_by_phid(self.phab, ambiguous_phids)
                            or ambiguous_phids,
                        )
                    )

                project_phid = project_name_to_id_map.get(project_name.lower(), None)

                if not project_phid:
                    raise PhabfiveRemoteException(
                        f"Project '{project_name}' is not found on the phabricator server"
                    )

                project_phids.append(project_phid)

            output["projects"] = project_phids

            # Translate the assignee and subscribers to the PHIDs resolved above
            assignment = output.get("assignment")

            if assignment:
                output["assignment"] = users[render(assignment)][0]

            output["subscribers"] = list(
                dict.fromkeys(
                    users[render(name)][0] for name in output.get("subscribers") or []
                )
            )

            # Translate the Space to its PHID, refusing a pattern that names
            # more than one the way --space does
            r(output, "space", variables)
            space_name = output.get("space")

            if space_name:
                output["space"] = space_phid_for(space_name)

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
                # Validated and normalized by validate_priorities above; a
                # `priority:` with nothing after it is no priority at all
                add_transaction(
                    transactions,
                    "priority",
                    task_config.get("priority") or PRIORITY_DEFAULT,
                )

                assignment = task_config.get("assignment")

                if assignment:
                    add_transaction(transactions, "owner", assignment)

                projects = task_config.get("projects", [])

                if projects:
                    add_transaction(transactions, "projects.set", projects)

                subscribers = task_config.get("subscribers", [])

                if subscribers:
                    add_transaction(transactions, "subscribers.set", subscribers)

                space_phid = task_config.get("space")

                if space_phid:
                    add_transaction(transactions, "space", space_phid)

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

        # The IDs of the tasks actually created, in the order the recursion
        # created them. Without this the method returned None on a real run
        # and a caller had no way to name what it had just made, which is
        # why `--with` could not answer `--format` (#344).
        created_ids = []

        def recurse_commit_transactions(task_config, parent_task_config, depth=0):
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
                    # Extract title from transactions for display
                    title = next(
                        (
                            t["value"]
                            for t in transactions_to_commit
                            if t["type"] == "title"
                        ),
                        "<no title>",
                    )
                    values = {t["type"]: t["value"] for t in transactions_to_commit}
                    owner = values.get("owner")
                    dry_run_tasks.append(
                        {
                            "depth": depth,
                            "title": title,
                            "assignee": user_names[owner] if owner else None,
                            "subscribers": [
                                user_names[phid]
                                for phid in values.get("subscribers.set", [])
                            ],
                        }
                    )
                else:
                    result = self.phab.maniphest.edit(
                        transactions=transactions_to_commit,
                    )

                    # Store the newly created ticket ID in the data structure so child tickets can look it up
                    task_config["phid"] = str(result["object"]["phid"])
                    created_ids.append(result["object"]["id"])
            child_tasks = task_config.get("tasks", None)

            if not transactions_to_commit and not child_tasks:
                log.warning(
                    "No transactions to commit and no child tasks - possible data issue"
                )

            if child_tasks:
                for child_task in child_tasks:
                    recurse_commit_transactions(child_task, task_config, depth + 1)

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

        return {"task_ids": created_ids}

    def create_task(
        self,
        title,
        description=None,
        tags=None,
        assignee=None,
        status=None,
        priority=None,
        subscribers=None,
        column=None,
        board_phid=None,
        space=None,
        visible_to=None,
        editable_by=None,
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
            Project names, hashtags, IDs or PHIDs. Each item may hold several,
            separated by commas
        assignee : str, optional
            Username of the assignee (supports @me for current user)
        status : str, optional
            Task status (Open, Resolved, etc.)
        priority : str, optional
            Task priority (Unbreak, Triage, High, Normal, Low, Wish)
        subscribers : list, optional
            Subscriber usernames (supports @me for current user). Each item
            may hold several, separated by commas
        column : str, optional
            Column name on board for initial placement
        board_phid : str, optional
            Board PHID for column placement (required if column is specified)
        space : str, optional
            Space to create the task in, by monogram, name, or a pattern that
            matches exactly one Space. The server's default Space is used when
            omitted; PHAB_SPACE filters searches and is deliberately not
            consulted here.
        visible_to : str, optional
            Who can see the new task, in the grammar phabfive.policy accepts.
            The server's default is used when omitted.
        editable_by : str, optional
            Who can edit the new task. Both are named after the labels
            Phorge's own form uses.
        dry_run : bool
            If True, validate and display without creating

        Returns
        -------
        dict or None
            Task info dict with 'phid', 'id', 'uri' keys, or None if dry_run

        Raises
        ------
        PhabfiveConfigException
            If validation fails (invalid priority, status, user not found, or a
            policy value outside the grammar)
        PhabfiveDataException
            If a policy names a project or user that does not exist
        PhabfiveRemoteException
            If API call fails
        """
        # Refused here rather than by the API, which reads a value it does not
        # recognise as a policy nobody satisfies and so answers a typo with a
        # permissions error.
        validate_policy_value(visible_to, option="--visible-to")
        validate_policy_value(editable_by, option="--editable-by")

        # Repeatable and comma-separated, like every other list of values
        parsed_tags = split_list_option(tags)
        parsed_subscribers = split_list_option(subscribers)

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

        # Resolve assignee username to PHID (supports @me shortcut)
        assignee_display = assignee
        if assignee:
            [(assignee_phid, username)] = self._resolve_users(
                [assignee], option="--assign"
            ).values()
            assignee_display = username or assignee
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

        # Resolve subscribers to PHIDs (usernames, @me or PHIDs)
        subscriber_display = []
        if parsed_subscribers:
            users = self._resolve_users(parsed_subscribers, option="--subscribe")
            subscriber_phids = list(dict.fromkeys(phid for phid, _ in users.values()))
            subscriber_display = [
                username or value for value, (_, username) in users.items()
            ]
            if subscriber_phids:
                transactions.append(
                    {"type": "subscribers.set", "value": subscriber_phids}
                )
        else:
            subscriber_display = parsed_subscribers

        # Resolve the Space to place the task in. Filtering may name several
        # Spaces at once; creating in one cannot, so this demands exactly one.
        space_display = None
        if space:
            resolved_space = self._resolve_space(space)
            space_display = describe_space(resolved_space)
            transactions.append({"type": "space", "value": resolved_space["phid"]})

        # Policies. There is no interact one to set: a task derives "Can
        # Interact With" from its view policy rather than storing it.
        resolved_policies = [
            (label, key, resolve_policy_value(self.phab, value, option=option))
            for key, value, label, option in (
                ("view", visible_to, "Visible To", "--visible-to"),
                ("edit", editable_by, "Editable By", "--editable-by"),
            )
            if value is not None
        ]

        for _, key, resolved in resolved_policies:
            transactions.append(
                {"type": TASK_POLICY_TRANSACTIONS[key], "value": resolved}
            )

        # One lookup for both, so naming what the dry run reports costs at most
        # a single round trip.
        policy_names = resolve_policy_names(
            self.phab, [resolved for _, _, resolved in resolved_policies]
        )
        policy_display = {
            label: policy_label(resolved, policy_names)
            for label, _, resolved in resolved_policies
        }

        # Dry run - return what would be created
        if dry_run:
            log.info("Dry run mode - task would be created with these transactions:")
            return {
                "dry_run": True,
                "title": title,
                "description": description,
                "priority": priority,
                "status": status,
                "assignee": assignee_display,
                "tags": parsed_tags,
                "column": column,
                "subscribers": subscriber_display,
                "space": space_display,
                "policy": policy_display,
            }

        # Create the task via API
        try:
            result = self.phab.maniphest.edit(transactions=transactions)
            task_object = result["object"]

            # Fetch the task to get the URI
            task_id = task_object["id"]
            task_info = self.get_task_info(task_id)
            task_uri = task_info.get("uri", f"{self.url}T{task_id}")

            # Extract base URL from task URI for building tag URLs
            # e.g., "http://phorge.domain.tld/T5" -> "http://phorge.domain.tld"
            base_url = task_uri.rsplit("/", 1)[0] if "/" in task_uri else self.url

            # Move task to specified column if requested
            if column and board_phid:
                task_data = self._get_task_data(task_id)
                column_phid = self._navigate_column(
                    task_id, task_data, column, board_phid
                )
                self.phab.maniphest.edit(
                    objectIdentifier=f"T{task_id}",
                    transactions=[{"type": "column", "value": [column_phid]}],
                )

            return {
                "phid": task_object["phid"],
                "id": task_id,
                "uri": task_uri,
                "tag_slugs": project_slugs,
                "base_url": base_url,
            }
        except Exception as e:
            raise PhabfiveRemoteException(f"Failed to create task: {e}")

    def _get_task_data(self, task_id):
        """Fetch task data with all necessary attachments for editing.

        Parameters
        ----------
        task_id : str
            Numeric task ID (e.g., "123")

        Returns
        -------
        dict
            Task data from API with attachments

        Raises
        ------
        PhabfiveNotFoundException
            If task not found
        """
        result = self.phab.maniphest.search(
            constraints={"ids": [int(task_id)]},
            attachments={"columns": True, "projects": True, "subscribers": True},
        )

        if not result["data"]:
            raise PhabfiveNotFoundException(f"Task T{task_id} not found")

        return result["data"][0]

    def build_task_edit(
        self,
        task_id,
        task_data=None,
        title=None,
        priority=None,
        status=None,
        board_phid=None,
        column=None,
        assign=None,
        description=None,
        subscribe=None,
        comment=None,
        space=None,
        visible_to=None,
        editable_by=None,
    ):
        """Compute the transactions for a task edit, without applying them.

        Parameters
        ----------
        task_id : str
            Numeric task ID (e.g., "123")
        task_data : dict, optional
            Already-fetched task data. Fetched here when omitted, so a caller
            that has it already does not pay for a second round trip.
        title : str, optional
            New title for the task
        priority : str, optional
            Priority to set or "raise"/"lower"
        status : str, optional
            Status to set
        board_phid : str, optional
            Board PHID for column context
        column : str, optional
            Column name or "forward"/"backward"
        assign : str, optional
            Username to assign
        description : str, optional
            Description text to set
        subscribe : list, optional
            Usernames to add as subscribers (@me for current user)
        comment : str, optional
            Comment to add
        space : str, optional
            Space to move the task to, by monogram, name, or a pattern that
            matches exactly one Space
        visible_to : str, optional
            New view policy, in the grammar phabfive.policy accepts
        editable_by : str, optional
            New edit policy. Both are named after the labels Phorge's own
            form uses.

        Returns
        -------
        tuple
            (transactions, changes) - the Conduit transactions to apply and a
            human-readable description of each one.

        Raises
        ------
        PhabfiveInputException
            On an argument value that cannot be used
        PhabfiveNotFoundException
            On a task, user or column that does not exist
        PhabfiveConfigException
            If a policy value is outside the grammar
        PhabfiveDataException
            If a policy names a project or user that does not exist
        """
        # Fetch current task state unless the caller already has it
        if task_data is None:
            task_data = self._get_task_data(task_id)
        current_priority = task_data["fields"]["priority"]["value"]
        current_priority_name = task_data["fields"]["priority"].get("name", "Unknown")
        current_status = task_data["fields"]["status"]["value"]
        current_status_name = task_data["fields"]["status"].get("name", current_status)
        current_title = task_data["fields"].get("name", "")

        transactions = []
        changes = []  # Track human-readable changes for display

        # Handle title
        if title is not None and title != current_title:
            transactions.append({"type": "title", "value": title})
            changes.append(
                {
                    "field": "Title",
                    "old": current_title,
                    "new": title,
                }
            )

        # Handle priority
        if priority:
            if priority.lower() in ("raise", "higher", "lower"):
                # Normalize "higher" to "raise"
                direction = (
                    "raise" if priority.lower() in ("raise", "higher") else "lower"
                )
                new_priority = self._navigate_priority(current_priority, direction)
            else:
                new_priority = self._validate_priority(priority)

            # Convert string priority to numeric for comparison
            from phabfive.constants import PRIORITY_VALUES

            new_priority_numeric = (
                PRIORITY_VALUES.get(new_priority, new_priority)
                if isinstance(new_priority, str)
                else new_priority
            )

            if new_priority_numeric != current_priority:
                transactions.append({"type": "priority", "value": new_priority})
                # Get human-readable name for new priority
                # new_priority is either a numeric value (from raise/lower) or
                # a string like "high" (from validate_priority)
                priority_map = self._get_api_priority_map()
                # Map API string values to display names
                api_to_display = {
                    "unbreak": "Unbreak Now!",
                    "triage": "Triage",
                    "high": "High",
                    "normal": "Normal",
                    "low": "Low",
                    "wish": "Wishlist",
                }
                if isinstance(new_priority, int):
                    new_priority_name = priority_map.get(
                        new_priority,
                        priority_map.get(str(new_priority), str(new_priority)),
                    )
                else:
                    new_priority_name = api_to_display.get(
                        str(new_priority).lower(), str(new_priority)
                    )
                changes.append(
                    {
                        "field": "Priority",
                        "old": current_priority_name,
                        "new": new_priority_name,
                    }
                )

        # Handle status
        if status:
            validated_status = self._validate_status(status)
            if validated_status != current_status:
                transactions.append({"type": "status", "value": validated_status})
                # Get human-readable name for new status
                status_map = self._get_api_status_map().get("statusMap", {})
                status_entry = status_map.get(validated_status, validated_status)
                # statusMap values can be strings or dicts with "name" key
                if isinstance(status_entry, dict):
                    new_status_name = status_entry.get("name", validated_status)
                else:
                    new_status_name = status_entry
                changes.append(
                    {
                        "field": "Status",
                        "old": current_status_name,
                        "new": new_status_name,
                    }
                )

        # Handle column
        if column and board_phid:
            column_phid = self._navigate_column(task_id, task_data, column, board_phid)
            if column_phid:
                # Get current and new column names
                from phabfive.maniphest.fetchers import get_column_info

                column_info = get_column_info(self.phab, board_phid)
                new_column_name = column_info.get(column_phid, {}).get("name", column)

                # Get the current column on this board. The PHID is what the
                # target is compared against, not the name: two boards can
                # both have a "Backlog", and a task can be on both.
                current_col_phid = None
                current_column_name = None
                boards_data = (
                    task_data.get("attachments", {})
                    .get("columns", {})
                    .get("boards", {})
                )
                if board_phid in boards_data:
                    current_columns = boards_data[board_phid].get("columns", [])
                    if current_columns:
                        current_col_phid = current_columns[0].get("phid")
                        current_column_name = column_info.get(current_col_phid, {}).get(
                            "name"
                        )

                # Also need to add task to board if not already on it. This
                # stays outside the comparison below: putting a task on a
                # board and into a column is one edit.
                task_projects = task_data["attachments"]["projects"]["projectPHIDs"]
                if board_phid not in task_projects:
                    transactions.append({"type": "projects.add", "value": [board_phid]})

                # A task already in the target column needs no transaction,
                # and reporting one would claim a move that never happened.
                if column_phid != current_col_phid:
                    transactions.append({"type": "column", "value": [column_phid]})

                    changes.append(
                        {
                            "field": "Column",
                            "old": current_column_name or "(none)",
                            "new": new_column_name,
                        }
                    )

        # Handle assignee
        if assign:
            [(user_phid, username)] = self._resolve_users(
                [assign], option="--assign"
            ).values()
            new_username = username or assign
            current_owner = task_data["fields"]["ownerPHID"]
            if user_phid != current_owner:
                transactions.append({"type": "owner", "value": user_phid})
                # Get current owner username
                current_username = None
                if current_owner:
                    try:
                        result = self.phab.user.search(
                            constraints={"phids": [current_owner]}
                        )
                        if result.get("data"):
                            current_username = result["data"][0]["fields"].get(
                                "username"
                            )
                    except Exception:
                        pass
                changes.append(
                    {
                        "field": "Assignee",
                        "old": current_username or "(none)",
                        "new": new_username,
                    }
                )

        # Handle description
        if description is not None:
            current_desc = task_data["fields"].get("description", {}).get("raw", "")
            if description != current_desc:
                transactions.append({"type": "description", "value": description})
                changes.append(
                    {
                        "field": "Description",
                        "old": self._format_description_preview(current_desc),
                        "new": self._format_description_preview(description),
                    }
                )

        # Handle comment
        if comment:
            transactions.append({"type": "comment", "value": comment})
            changes.append({"field": "Comment", "old": None, "new": "Added"})

        # Handle subscribers
        if subscribe:
            # Get current subscribers
            current_subscribers = set(
                task_data.get("attachments", {})
                .get("subscribers", {})
                .get("subscriberPHIDs", [])
            )

            subscriber_phids = []
            subscriber_names = []
            users = self._resolve_users(
                split_list_option(subscribe), option="--subscribe"
            )
            for value, (user_phid, username) in users.items():
                # Only add if not already subscribed
                if (
                    user_phid not in current_subscribers
                    and user_phid not in subscriber_phids
                ):
                    subscriber_phids.append(user_phid)
                    subscriber_names.append(username or value)

            if subscriber_phids:
                transactions.append(
                    {"type": "subscribers.add", "value": subscriber_phids}
                )
                changes.append(
                    {
                        "field": "Subscribers",
                        "old": None,
                        "new": f"Added: {', '.join(subscriber_names)}",
                    }
                )

        # Handle space
        if space:
            # Enumerated once per command, which also names the Space the task
            # is leaving without a lookup of its own.
            all_spaces = self._get_all_spaces()
            resolved_space = self._resolve_space(space, all_spaces=all_spaces)
            current_space_phid = task_data["fields"].get("spacePHID")

            if resolved_space["phid"] != current_space_phid:
                transactions.append({"type": "space", "value": resolved_space["phid"]})
                changes.append(
                    {
                        "field": "Space",
                        "old": describe_space_phid(
                            self.phab, current_space_phid, all_spaces
                        )
                        or "(none)",
                        "new": describe_space(resolved_space),
                    }
                )

        policy_transactions, policy_changes = self._build_policy_edit(
            task_data["fields"].get("policy") or {},
            visible_to=visible_to,
            editable_by=editable_by,
        )

        return transactions + policy_transactions, changes + policy_changes

    def _build_policy_edit(self, policy, visible_to=None, editable_by=None):
        """The policy half of a task edit.

        Kept apart from the scalar fields because a policy is not a scalar:
        each value asked for has to be resolved against the instance before
        it can be compared with the one in place, and both ends of the
        comparison are then named for the dry run - a PHID either side of an
        arrow says nothing about what changed.

        There is no interact half. A task's "Can Interact With" is derived
        rather than stored - see TASK_POLICY_TRANSACTIONS - so maniphest.edit
        has no transaction that could set it.

        Parameters
        ----------
        policy : dict
            The "policy" field of the task as it stands
        visible_to, editable_by : str, optional
            New policies, in the grammar phabfive.policy accepts

        Returns
        -------
        tuple
            (transactions, changes)
        """
        asked = [
            ("view", visible_to, "Visible To", "--visible-to"),
            ("edit", editable_by, "Editable By", "--editable-by"),
        ]

        wanted = [
            (
                key,
                label,
                policy.get(TASK_POLICY_FIELDS[key]),
                resolve_policy_value(self.phab, value, option=option),
            )
            for key, value, label, option in asked
            if value is not None
        ]

        if not wanted:
            return [], []

        # One lookup for both ends of every arrow: the policy in place and
        # the one asked for are named out of the same phid.query.
        names = resolve_policy_names(
            self.phab,
            [current for _, _, current, _ in wanted] + [new for _, _, _, new in wanted],
        )

        transactions = []
        changes = []

        for key, label, current, new in wanted:
            if current == new:
                # Already at the target policy.
                continue

            transactions.append({"type": TASK_POLICY_TRANSACTIONS[key], "value": new})

            changes.append(
                {
                    "field": label,
                    "old": policy_label(current, names),
                    "new": policy_label(new, names),
                }
            )

        return transactions, changes

    def edit_task_by_id(
        self,
        task_id,
        title=None,
        priority=None,
        status=None,
        board_phid=None,
        column=None,
        assign=None,
        description=None,
        subscribe=None,
        comment=None,
        space=None,
        visible_to=None,
        editable_by=None,
        dry_run=False,
        task_data=None,
    ):
        """Edit a task by ID.

        Thin composition of :meth:`build_task_edit` and the Conduit call, so a
        caller that wants to show the changes before applying them can stop in
        between.

        Parameters
        ----------
        task_id : str
            Numeric task ID (e.g., "123")
        visible_to : str, optional
            New view policy
        editable_by : str, optional
            New edit policy
        dry_run : bool
            Show changes without applying
        task_data : dict, optional
            Already-fetched task data, passed through to build_task_edit

        Returns
        -------
        dict
            {"task_id", "changes"}, plus "dry_run": True for a dry run, which
            builds the changes and sends nothing. Nothing is printed; a
            caller that wants a preview renders "changes" itself.

        Raises
        ------
        PhabfiveException
            As build_task_edit and apply_task_edit raise
        """
        transactions, changes = self.build_task_edit(
            task_id,
            task_data,
            title=title,
            priority=priority,
            status=status,
            board_phid=board_phid,
            column=column,
            assign=assign,
            description=description,
            subscribe=subscribe,
            comment=comment,
            space=space,
            visible_to=visible_to,
            editable_by=editable_by,
        )

        if not transactions:
            log.info(f"No changes to apply for T{task_id}")
            return {"task_id": task_id, "changes": []}

        if dry_run:
            return {"task_id": task_id, "changes": changes, "dry_run": True}

        self.apply_task_edit(task_id, transactions)

        return {"task_id": task_id, "changes": changes}

    def apply_task_edit(self, task_id, transactions):
        """Send prepared transactions to Maniphest.

        Parameters
        ----------
        task_id : str
            Numeric task ID (e.g., "123")
        transactions : list
            Transactions from :meth:`build_task_edit`

        Raises
        ------
        PhabfiveDataException
            If the API rejects the edit. A policy that would take the task
            away from whoever is applying it is rejected this way, and is
            reported as the sentence Phorge answered with rather than as the
            PhabfiveAPIException around it.

        An edit that only sets fields is retried on a timeout or a 5xx like
        a read, since arriving twice changes nothing. One carrying a comment
        is not, since it would post the comment twice.
        """
        try:
            with idempotent_writes(is_idempotent_edit(transactions)):
                self.phab.maniphest.edit(
                    objectIdentifier=f"T{task_id}", transactions=transactions
                )
        except PhabfiveAPIException as e:
            raise PhabfiveDataException(policy_lockout_message(e) or str(e))

    def _format_description_preview(self, text):
        """Format description for change display.

        Shows first line (truncated) with line/char count for multiline text.

        Parameters
        ----------
        text : str
            Description text

        Returns
        -------
        str
            Formatted preview string
        """
        if not text:
            return "(empty)"

        lines = text.split("\n")
        first_line = lines[0].strip()

        # Truncate first line if too long
        if len(first_line) > 50:
            first_line = first_line[:47] + "..."

        # Add stats for multiline
        if len(lines) > 1:
            total_chars = len(text)
            return f"{first_line} ({len(lines)} lines, {total_chars} chars)"
        elif len(text) > 50:
            return f"{first_line} ({len(text)} chars)"
        else:
            return first_line or "(empty)"

    def _navigate_priority(self, current_priority, direction):
        """Navigate priority up or down, skipping Triage.

        Priority ladder (skipping Triage):
        Wish(0) -> Low(25) -> Normal(50) -> High(80) -> Unbreak(100)
        Triage(90) is excluded and can only be set explicitly.

        Parameters
        ----------
        current_priority : int
            Current priority value
        direction : str
            "raise" or "lower"

        Returns
        -------
        str
            New priority key (e.g., "high")
        """
        from phabfive.constants import PRIORITY_VALUES

        # Build priority ladder excluding Triage, sorted by value
        # Triage (90) is excluded and can only be set explicitly
        ladder = sorted(
            [(v, k) for k, v in PRIORITY_VALUES.items() if k != "triage"],
            key=lambda x: x[0],
        )

        # Special handling for Triage (90) - it sits between High and Unbreak
        # but is excluded from raise/lower navigation
        if current_priority == PRIORITY_VALUES.get("triage", 90):
            if direction == "raise":
                return "unbreak"  # Skip directly to Unbreak
            else:  # lower
                return "high"  # Skip directly to High

        current_idx = None
        for idx, (val, key) in enumerate(ladder):
            if val == current_priority:
                current_idx = idx
                break

        if current_idx is None:
            # Unknown priority, default to normal
            return "normal"

        if direction == "raise":
            new_idx = min(current_idx + 1, len(ladder) - 1)
        else:  # lower
            new_idx = max(current_idx - 1, 0)

        return ladder[new_idx][1]

    def _navigate_column(self, task_id, task_data, column_name, board_phid):
        """Navigate to a column by name or direction.

        Parameters
        ----------
        task_id : str
            Task ID for error messages
        task_data : dict
            Current task data
        column_name : str
            Column name or "forward"/"backward"
        board_phid : str
            Board PHID

        Returns
        -------
        str
            Column PHID or None if no change

        Raises
        ------
        PhabfiveNotFoundException
            If the column is not on the board
        PhabfiveDataException
            If the task is in no column on the board to navigate from
        """
        from phabfive.maniphest.fetchers import get_column_info

        # Get column info for the board
        column_info = get_column_info(self.phab, board_phid)

        if column_name.lower() in ("forward", "next", "backward", "previous"):
            # Get current column on this board
            current_column_phid = None
            boards = (
                task_data.get("attachments", {}).get("columns", {}).get("boards", {})
            )
            if board_phid in boards:
                columns = boards[board_phid].get("columns", [])
                if columns:
                    current_column_phid = columns[0]["phid"]

            if not current_column_phid:
                raise PhabfiveDataException(
                    f"Task T{task_id} is not currently in any column on this board"
                )

            # Find current position
            current_info = column_info.get(current_column_phid)
            if not current_info:
                raise PhabfiveDataException(
                    "Could not determine current column position"
                )

            current_seq = current_info["sequence"]

            # Navigate by sorting columns by sequence
            sorted_cols = sorted(column_info.items(), key=lambda x: x[1]["sequence"])

            if column_name.lower() in ("forward", "next"):
                for col_phid, col_data in sorted_cols:
                    if col_data["sequence"] > current_seq:
                        return col_phid
                # Already at end, stay
                return current_column_phid

            else:  # backward, previous
                for col_phid, col_data in reversed(sorted_cols):
                    if col_data["sequence"] < current_seq:
                        return col_phid
                # Already at start, stay
                return current_column_phid

        else:
            # Exact column name - search by name
            for col_phid, col_data in column_info.items():
                if col_data["name"].lower() == column_name.lower():
                    return col_phid

            # Not found
            available = [col_data["name"] for col_data in column_info.values()]
            raise PhabfiveNotFoundException(
                f"Column '{column_name}' not found on board. Available: {available}"
            )


__all__ = ["Maniphest"]
