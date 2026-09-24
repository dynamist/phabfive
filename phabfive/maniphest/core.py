# -*- coding: utf-8 -*-

"""Main Maniphest class that orchestrates all submodules."""

import itertools
import logging
import functools
from pathlib import Path

from ruamel.yaml import YAML

from phabfive.commits import fetch_commit_handles, resolve_commit_phids
from phabfive.constants import (
    MANIPHEST_ORDER_DEFAULT,
    MANIPHEST_ORDER_DIRECTIONS,
    MANIPHEST_ORDER_FIELDS,
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
    PhabfiveInputException,
    PhabfiveNotFoundException,
    PhabfiveRemoteException,
)
from phabfive.maniphest.fetchers import (
    fetch_all_transactions,
    fetch_project_names_for_boards,
    fetch_task_edges,
    fetch_task_relationships,
    fallback_status_map,
    fetch_api_priority_values,
    fetch_api_status_map,
    get_api_priority_map,
    get_column_info,
)
from phabfive.maniphest.filters import (
    task_matches_any_pattern,
    task_matches_priority_patterns,
    task_matches_project_patterns,
    task_matches_status_patterns,
)
from phabfive.maniphest.formatters import build_task_boards, build_task_display_data
from phabfive.maniphest.resolvers import (
    describe_space,
    describe_space_phid,
    fetch_all_spaces,
    is_exact_monogram,
    fetch_projects_by_phid,
    resolve_project_phids,
    resolve_project_phids_for_create,
    resolve_project_tags,
    resolve_space,
    resolve_space_phids,
)
from phabfive.maniphest.utils import (
    PHORGE_ORDER_KEYS,
    days_ago_to_timestamp,
    sort_tasks,
)
from phabfive.pagination import iter_pages, search_all_pages
from phabfive.spec.registry import constraint_for, field_by_name, fields_for
from phabfive.spec.times import parse_time_with_unit
from phabfive.ordering import parse_order
from phabfive.maniphest.validators import (
    validate_assignment,
    validate_priority,
    validate_status,
)
from phabfive.me import is_me
from phabfive.options import split_list_option, value_list
from phabfive.policy import (
    policy_label,
    policy_lockout_message,
    resolve_policy_names,
    resolve_policy_value,
    validate_policy_value,
)
from phabfive.project_filters import parse_project_patterns
from phabfive.retry import idempotent_writes, is_idempotent_edit
from phabfive.users import resolve_user_phids as resolve_users, user_list_edit

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


#: The constraints `task_search` sends only as an optimisation, and which it
#: may therefore drop and ask again without. Each is a *lift*: a filter that
#: is applied in Python either way, sent as a constraint so the server
#: narrows what crosses the wire. An instance that does not know one of them
#: (#434 - Phabricator and Phorge do not answer the same constraints) gets
#: the same search without it, rather than an error for a search that used
#: to work. Every other constraint decides results and must never be dropped.
OPTIONAL_CONSTRAINTS = ("priorities", "columnPHIDs")

#: Conduit's answer when an instance does not know a constraint.
INVALID_CONSTRAINT = "ERR-INVALID-CONSTRAINT"


def _search_key_for_constraint():
    """Conduit constraint name -> the search key a person wrote for it.

    So that an instance refusing ``closerPHIDs`` can be answered with
    ``closed-by``, which is the half of the exchange the user chose. Built
    from the registry rather than restated, so a key declared there cannot
    drift out of the message.
    """
    keys = {}

    for field in fields_for("task", "search"):
        constraint = constraint_for(field, "task")
        if constraint:
            keys.setdefault(constraint, field.name)

    return keys


def _value_list(value):
    """A comma-separated string, or a list, as a list of strings.

    The reading is `phabfive.options.value_list`, which every app's list
    filters share. The one thing maniphest wants on top is ``None`` rather
    than an empty list for nothing at all: an empty constraint list is a
    filter matching nothing, which is never what an absent option meant.
    """
    return value_list(value) or None


def _task_id_list(value, option):
    """Task monograms as the integers ``maniphest.search`` constrains on.

    `phabfive.spec.search.task_ids` itself, which is the one grammar every
    key that takes a task id reads: ``"T123,T456"``, ``["T123", "T456"]``
    and a list whose entries hold commas. Not a second copy of it - this one
    used to accept a bare ``123`` while ``--include`` refused the same value
    on the same command, and `monograms=("T",)` in the registry is what both
    are declared as.

    Raises
    ------
    PhabfiveInputException
        For anything that is not a task monogram. Another application's
        monogram is not a task, so ``P45`` is refused by name rather than
        sent as the id 45.
    """
    from phabfive.spec.search import task_ids

    return task_ids(value, option=option)


def _lift_types(key):
    """Which condition types of one search key may become a constraint.

    Read off the `Field` rather than written here: `Field.lifts` is where
    the rule is declared, and a declaration nothing reads is a comment with
    a mirror test. Changing a field's `lifts` to `()` therefore really does
    stop that filter being narrowed server-side.
    """
    field = field_by_name(key, "task", "search")

    return field.lifts if field is not None else ()


def current_state_targets(patterns, key, lifts=None):
    """The values a transition filter demands an object currently be in.

    A pattern's conditions are ANDed, so a pattern holding a non-negated
    ``in:`` condition can only match objects whose current state is that
    condition's value, whatever else the pattern also asks about. When
    *every* pattern has one, the union over the patterns is a superset of
    what the filter will keep - which is exactly what a constraint may
    narrow the fetch to, with the filter still deciding.

    A pattern without one - ``been:High``, ``from:Low``, ``raised``,
    ``not:in:Low`` - can match an object in any current state, so there is
    nothing to narrow with and the answer is ``None``: all or nothing,
    because narrowing on behalf of only some of an OR would drop matches.

    Parameters
    ----------
    patterns : list or None
        ColumnPattern, PriorityPattern or StatusPattern objects.
    key : str
        The condition key holding the value, i.e. "column", "priority" or
        "status".
    lifts : sequence of str, optional
        The condition types that name a current state. The default is the
        key's own `Field.lifts` from `phabfive.spec.registry`, which is the
        single declaration of this rule; an empty one means the filter is
        never narrowed.

    Returns
    -------
    list or None
        The distinct values, in the order the patterns name them, or None
        when the filter cannot be narrowed.
    """
    if not patterns:
        return None

    liftable = frozenset(_lift_types(key) if lifts is None else lifts)

    if not liftable:
        return None

    targets = []

    for pattern in patterns:
        wanted = [
            condition[key]
            for condition in pattern.conditions
            if condition.get("type") in liftable
            and not condition.get("negated")
            and condition.get(key)
        ]

        if not wanted:
            return None

        for value in wanted:
            if value not in targets:
                targets.append(value)

    return targets


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

    def _resolve_project_tags(self, values, option=None):
        """Resolve the projects an edit adds or removes to (PHID, name), exactly."""
        return resolve_project_tags(self.phab, values, option=option)

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
    def _get_api_priority_values(self):
        """This instance's own priority spellings, name and keyword to value.

        Asked of `maniphest.priority.search` rather than read off
        `get_api_priority_map`, which is a constant: an instance that
        relabelled a priority - or Phorge's own stock table, which calls 90
        "Needs Triage" and not "Triage" - is not described by it, and
        narrowing a search by a value guessed from it returns the wrong
        tasks silently.

        An empty mapping means the question could not be answered, and the
        one caller answers that by not narrowing at all.
        """
        try:
            return fetch_api_priority_values(self.phab)
        except Exception as e:
            log.debug(
                f"Could not read this instance's priorities ({e}); "
                "a --priority filter will not be narrowed server-side"
            )
            return {}

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
        commits_map=None,
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
            commits_map=commits_map,
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
        commits_map = {}

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

                commit_phids = fetch_task_relationships(self.phab, task_phid, "commits")
                commits_map[task_id] = (
                    fetch_commit_handles(self.phab, commit_phids)
                    if commit_phids
                    else []
                )

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
            commits_map=commits_map,
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
        Load search parameters from a spec file (supports multi-document).

        The reading is `phabfive.spec`'s: one loader for every spec phabfive
        holds, which is what lets a search template be written as YAML, JSON
        or TOML and read the same way. This adds only what the command has
        always been handed on top - the legacy per-search mapping, and the
        refusal of a key no `Field` declares.

        Parameters
        ----------
        template_path : str
            Path to the spec file containing search parameters

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
        from phabfive.spec import load_spec
        from phabfive.spec.envelope import DEFAULT_SEARCH_TYPE
        from phabfive.spec.search import check_search_params

        # kind="search" rather than inference: a search template may legally
        # carry only `title:` and `description:` with no `search:` key at
        # all, and nothing could tell that from a create spec.
        spec = load_spec(template_path, kind="search")

        # A spec's variables are rendered before the filters are read, the
        # same way `phabfive.cli.search_spec.load_search_spec` does it for the
        # other four `--with` commands. Without this a `{{ stale_days }}` in a
        # filter reached the parser as itself and was answered with "Invalid
        # time format: '{{ stale_days }}'", which says nothing about
        # variables at all. This path takes no `--set`, so a variable with no
        # default is still an error here - that is what `search -f` is for.
        if spec.variables:
            spec = spec.render()

        search_configs = []

        for index, item in enumerate(spec.items("search"), start=1):
            search_params = item.get("search", {})

            # Derived from the one Field declaration per key, so a key the
            # command reads and this refuses cannot happen again (#295).
            #
            # Only for the items this command could run. An item that says
            # `type: project` is refused by the planner, by name, with the
            # command that does run it - and checking its keys against the
            # *task* key set first would answer a project search with
            # "Unsupported search parameters: milestones", which is true of
            # nothing and says nothing about what is actually wrong. The
            # planner sees `type:` because it is carried through below.
            if (item.get("type") or DEFAULT_SEARCH_TYPE) == DEFAULT_SEARCH_TYPE:
                check_search_params(search_params, where=f"Document {index}")

            # Keep an omitted title distinct from a generated display label.
            # The CLI uses this to decide whether a single template was
            # explicitly named by its author.
            #
            # `type` is carried through, and it is load-bearing: dropping it
            # made `plan_search` see None, fall back to the default "task"
            # and run a `type: paste` item as a task search - a wrong answer
            # with a straight face. The planner's own guard can only refuse
            # what it is shown.
            search_configs.append(
                {
                    "type": item.get("type"),
                    "search": search_params,
                    "title": item.get("title"),
                    "description": item.get("description"),
                }
            )

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

    def _lifted_priorities(self, priority_patterns):
        """The ``priorities`` constraint a --priority filter can be narrowed by.

        ``--priority in:High`` asks about a task's current priority, which
        the server can answer; ``been:High``, ``from:Low``, ``raised`` and
        the rest ask about its history, which only the transaction log
        answers, so those keep fetching every task and filtering here. The
        pattern is checked per task either way - this only decides how many
        tasks are fetched to check.

        All or nothing: a priority name this phabfive cannot turn into the
        API's numeric value is left to the client-side filter, which compares
        the name the server gave. Lifting only the names that map would
        silently drop the tasks of the ones that did not.

        The name-to-value table is **this instance's**, from
        `maniphest.priority.search`, not the standard one in
        `get_api_priority_map`. The standard one is a constant, so it cannot
        see a relabelled priority: on an instance that calls value 50
        "High", it would map "High" to 80, fetch the value-80 tasks, and the
        client-side filter - which compares `fields.priority.name` - would
        then drop every one of them. An empty answer means the instance
        could not be asked, which is no lift rather than a guess.

        Returns
        -------
        list or None
            Priority values for the constraint, or None to send none.
        """
        targets = current_state_targets(priority_patterns, "priority")

        if not targets:
            return None

        values = self._get_api_priority_values()

        if not values:
            return None

        lifted = []
        for target in targets:
            value = values.get(str(target).lower())

            if value is None:
                log.debug(
                    f"Not narrowing the search by priority: '{target}' is not "
                    "a priority this instance names"
                )
                return None

            lifted.append(value)

        return sorted(set(lifted))

    def _lifted_statuses(self, status_patterns, status_scope):
        """The explicit ``statuses`` an --status filter can be narrowed by.

        As `_lifted_priorities`, with one extra rule: the keys are
        intersected with the scope the search would otherwise have asked
        for, so ``--status in:Resolved`` still reaches nothing while the
        scope is open - which is what it does today, and what
        `phabfive.transitions.status.unreachable_conditions` warns about.
        An empty intersection means no lift at all, leaving the scope's own
        list in place.

        Returns
        -------
        list or None
            Status keys for the constraint, or None for the scope's list.
        """
        targets = current_state_targets(status_patterns, "status")

        if not targets:
            return None

        status_map = self._get_api_status_map().get("statusMap") or {}
        keys = []

        for target in targets:
            wanted = str(target).lower()
            # By display name, which is what the pattern compares against,
            # and by key as well: a key that is not a name matches nothing
            # on the client, so asking for it only widens what is fetched.
            matched = [
                key
                for key, name in status_map.items()
                if str(name).lower() == wanted or str(key).lower() == wanted
            ]

            if not matched:
                return None

            keys.extend(matched)

        if status_scope == "open":
            allowed = set(self._get_open_statuses())
        elif status_scope == "closed":
            allowed = set(self._get_closed_statuses())
        else:
            allowed = None

        if allowed is not None:
            keys = [key for key in keys if key in allowed]

        return sorted(dict.fromkeys(keys)) or None

    def _lifted_column_phids(self, column_patterns, board_phids):
        """The ``columnPHIDs`` a --column filter can be narrowed by.

        Only with a board to resolve names on, which is why this needs the
        project PHIDs `--tag` resolved to: without one, the boards a task is
        on are read out of the task itself and are not known until it has
        been fetched. Names are matched exactly, as
        `phabfive.transitions.column.ColumnPattern._matches_current` does.

        Returns
        -------
        list or None
            Column PHIDs for the constraint, or None to send none.
        """
        targets = current_state_targets(column_patterns, "column")

        if not targets or not board_phids:
            return None

        by_name = {}
        for board_phid in board_phids:
            for column_phid, info in get_column_info(self.phab, board_phid).items():
                by_name.setdefault(info.get("name"), []).append(column_phid)

        lifted = []
        for target in targets:
            matched = by_name.get(target)

            if not matched:
                log.debug(
                    f"Not narrowing the search by column: no column named "
                    f"'{target}' on the board(s) being searched"
                )
                return None

            lifted.extend(matched)

        return sorted(dict.fromkeys(lifted))

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
        closed_after=None,
        closed_before=None,
        task_ids=None,
        task_phids=None,
        subscriber_phids=None,
        closer_phids=None,
        subtypes=None,
        parent_ids=None,
        subtask_ids=None,
        has_parents=None,
        has_subtasks=None,
        statuses=None,
        priorities=None,
        column_phids=None,
    ):
        """
        Build the shared constraints for a maniphest.search call.

        Every task_search code path applies the same filters; only the
        project selection differs, so callers add "projects" themselves.

        Parameters
        ----------
        status_scope : str
            "open", "closed" or "any" - which statuses the server is asked
            for when nothing narrower was worked out.
        statuses : list, optional
            Explicit status keys, which replace the scope's list. Worked out
            from an ``in:`` status pattern and already intersected with the
            scope, so it can only ever narrow what the scope would fetch.
        priorities : list, optional
            Priority values, from an ``in:`` priority pattern.
        column_phids : list, optional
            Workboard column PHIDs, from an ``in:`` column pattern.
        has_parents, has_subtasks : bool, optional
            Tri-state: None is no constraint, and False is a constraint
            asking for the tasks that have none.

        Returns
        -------
        dict
            Constraints accepted by maniphest.search. Note that the names
            are specific to this endpoint, see AGENTS.md.
        """
        constraints = {}

        if statuses:
            # A status pattern that asks about the current status, e.g.
            # "any+in:Resolved". Narrower than the scope it replaces and
            # never wider - see _lifted_statuses - and the pattern is still
            # checked per task, so this decides what is fetched, not what
            # matches.
            constraints["statuses"] = list(statuses)
            log.info(f"Filtering to statuses: {constraints['statuses']}")
        elif status_scope == "open":
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
        if closed_after:
            constraints["closedStart"] = int(closed_after)
        if closed_before:
            constraints["closedEnd"] = int(closed_before)

        if task_ids:
            constraints["ids"] = list(task_ids)
        if task_phids:
            constraints["phids"] = list(task_phids)
        if subscriber_phids:
            constraints["subscribers"] = list(subscriber_phids)
        if closer_phids:
            constraints["closerPHIDs"] = list(closer_phids)
        if subtypes:
            constraints["subtypes"] = list(subtypes)
        if parent_ids:
            constraints["parentIDs"] = list(parent_ids)
        if subtask_ids:
            constraints["subtaskIDs"] = list(subtask_ids)

        # Tri-state, so `is not None` rather than a truth test: False is
        # "the tasks with no parent at all", which is a filter and not the
        # absence of one.
        if has_parents is not None:
            constraints["hasParents"] = bool(has_parents)
        if has_subtasks is not None:
            constraints["hasSubtasks"] = bool(has_subtasks)

        if priorities:
            # A lift, like `statuses` above: the pattern still decides.
            constraints["priorities"] = list(priorities)
            log.info(f"Filtering to priorities: {constraints['priorities']}")
        if column_phids:
            constraints["columnPHIDs"] = list(column_phids)
            log.info(f"Filtering to {len(constraints['columnPHIDs'])} column(s)")

        return constraints

    def _search_page(self, **kwargs):
        """One maniphest.search response, as the payload paging reads.

        The client answers with a Result wrapping the payload; `.response`
        is that payload, and unwrapping it here is what lets one cursor
        loop - `phabfive.pagination` - serve maniphest as it serves every
        other app. A caller that already holds a plain mapping, which is
        what a stub hands back, is passed through unchanged.
        """
        result = self.phab.maniphest.search(**kwargs)

        return getattr(result, "response", result)

    def _search_all_pages(self, constraints, log_context="", order=None):
        """
        Run maniphest.search, following cursors until every page is read.

        The paging is `phabfive.pagination.iter_pages`, the one cursor loop
        every app shares, rather than a bespoke copy: maniphest was the last
        holdout. No total limit is passed, and deliberately - `--limit` is
        applied after the client-side sort, the policy filter, the
        transition filters and `--exclude`, so stopping early here would
        return a different set of tasks (see task_search).

        A constraint this instance does not know (#434: Phabricator and
        Phorge do not answer the same ones) is answered twice over. When it
        is one phabfive only sent to narrow the fetch, the search is asked
        again without it and the client-side filters still decide; when it
        is one that decides results, the error is translated into a sentence
        naming the constraint, the search key a person wrote for it and the
        instance, rather than surfaced as ERR-INVALID-CONSTRAINT.

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

        Raises
        ------
        PhabfiveDataException
            The instance does not accept a constraint this search needs.
        """
        attempt = dict(constraints)

        while True:
            try:
                return self._read_pages(attempt, log_context, order)
            except PhabfiveAPIException as error:
                if error.code != INVALID_CONSTRAINT:
                    raise

                refused = self._refused_constraint(attempt)

                if refused is None or refused not in OPTIONAL_CONSTRAINTS:
                    raise self._unknown_constraint(attempt, refused) from error

                log.warning(
                    f"This instance does not accept {refused}; searching "
                    "without it. The same tasks are found, but more of them "
                    "are fetched and filtered here."
                )

                # One key fewer each time round, so this ends: an instance
                # missing two of the optional constraints drops both.
                attempt = {
                    name: value for name, value in attempt.items() if name != refused
                }

    def _refused_constraint(self, constraints):
        """Which constraint this instance refused, found by asking again.

        Conduit names nothing. A real `maniphest.search` answers an unknown
        key with ERR-INVALID-CONSTRAINT and the sentence ``Parameter
        "constraints" includes an invalid key.`` - so a message that guessed
        from the code alone would name every constraint the search sent, most
        of which the instance accepts perfectly well, and tell the user to
        drop them.

        Asked instead, by bisection: half the constraints are sent on their
        own with ``limit=1``, and whichever half is refused is halved again.
        A search sending a dozen constraints costs four of these probes, each
        one row, and only on a path that has already failed.

        Parameters
        ----------
        constraints : dict
            What the failing search sent.

        Returns
        -------
        str or None
            The constraint name, or None when no subset reproduces the
            refusal - a bad value rather than a bad key, or an instance
            answering inconsistently. The caller then names them all, as
            candidates rather than as verdicts.
        """
        names = list(constraints)

        if len(names) <= 1:
            return names[0] if names else None

        return self._bisect_refused(constraints, names)

    def _bisect_refused(self, constraints, names):
        """The refused constraint within `names`, or None if none is."""
        if len(names) == 1:
            return names[0]

        middle = len(names) // 2

        for half in (names[:middle], names[middle:]):
            if self._refuses(constraints, half):
                return self._bisect_refused(constraints, half)

        return None

    def _refuses(self, constraints, names):
        """Whether one subset of the constraints is answered as invalid.

        Any other failure - a dead socket, a bad token, a value the endpoint
        dislikes - means these keys are not the ones being complained about,
        so it answers False and the bisection looks elsewhere. Nothing is
        raised out of here: this runs only to describe a failure that has
        already happened, and it must not replace it with a worse one.
        """
        subset = {name: constraints[name] for name in names}

        try:
            self.phab.maniphest.search(constraints=subset, limit=1)
        except PhabfiveAPIException as error:
            return error.code == INVALID_CONSTRAINT
        except Exception:  # pragma: no cover - defensive, see the docstring
            return False

        return False

    def _read_pages(self, constraints, log_context="", order=None):
        """Every page of one maniphest.search, concatenated.

        The columns attachment is asked for on every page, which is not
        free, and #478 wondered whether it could be conditional. It cannot,
        not yet: three separate readers need it and two of them are not the
        filters. `formatters.build_task_display_data` renders a task's
        "Boards" out of it for *every* displayed task, and
        `filters.task_matches_project_patterns` reads project membership off
        `boards` because the search is not asked for the projects
        attachment. So dropping it would quietly remove a section from the
        output and change which tasks a `--tag a+b` search keeps. Making it
        conditional means first giving those two readers a source of their
        own, which is its own change with its own test.
        """
        kwargs = {"constraints": constraints, "attachments": {"columns": True}}
        if order:
            kwargs["order"] = order

        tasks = []

        for page in iter_pages(self._search_page, **kwargs):
            tasks.extend(page)
            log.debug(
                f"{log_context}fetched page with {len(page)} tasks, "
                f"total so far: {len(tasks)}"
            )

        return tasks

    def _unknown_constraint(self, constraints, refused=None):
        """The error to raise for a constraint this instance does not have.

        Conduit's own answer is ERR-INVALID-CONSTRAINT and, on a real
        Phorge, ``Parameter "constraints" includes an invalid key.`` - which
        names neither the key nor anything the person typed. Phabricator and
        Phorge differ over which constraints exist and phabfive cannot yet
        ask an instance which it has (#434), so `_refused_constraint` asks
        the only way there is, and this says what came back.

        Two sentences, because the two cases are not the same claim: one
        constraint was identified and is named as the culprit, or none was
        and the search's constraints are listed as *candidates*. Telling a
        user to drop a key this instance accepts is worse than saying the
        instance would not say which.

        Parameters
        ----------
        constraints : dict
            What the failing search sent.
        refused : str, optional
            The constraint the instance refused, when it could be isolated.
        """
        keys = _search_key_for_constraint()
        instance = self.conf.get("PHAB_URL") or "this instance"

        def described(name):
            return f"'{name}' (from '{keys[name]}')" if name in keys else f"'{name}'"

        if refused is not None:
            return PhabfiveDataException(
                f"maniphest.search on {instance} does not accept the "
                f"constraint {described(refused)}. Phabricator and Phorge do "
                "not answer the same constraints; drop that search key, or "
                "use an instance that has it."
            )

        listed = ", ".join(described(name) for name in sorted(constraints))

        return PhabfiveDataException(
            f"maniphest.search on {instance} refused one of this search's "
            f"constraints and did not say which. It sent {listed}. "
            "Phabricator and Phorge do not answer the same constraints; "
            "drop the search keys one at a time to find it, or use an "
            "instance that has them."
        )

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
        closed_after=None,
        closed_before=None,
        ids=None,
        phids=None,
        subscriber=None,
        subtype=None,
        parent=None,
        subtask=None,
        has_parents=None,
        has_subtasks=None,
        closed_by=None,
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
        closed_after  (str|int, optional): Tasks closed within TIME, as created_after.
        closed_before (str|int, optional): Tasks closed more than TIME ago.
        ids           (str|list, optional): Only these tasks ("T1,T2", ["T1"], 1), with every
                      other filter still applied. The opposite of include_task_ids, which
                      bypasses the filters; asking for both is an intersection and a union
                      at once, which is exactly what each of them says.
        phids         (str|list, optional): Only these task PHIDs, as ids.
        subscriber    (str, optional): Only tasks a user is subscribed to. Use "@me", a
                      username or a PHID; comma-separated for OR logic.
        subtype       (str|list, optional): Only tasks of these subtype keys. Instance
                      configuration, so the value is sent as written.
        parent        (str|list, optional): Only the subtasks of these tasks.
        subtask       (str|list, optional): Only the parents of these tasks.
        has_parents   (bool, optional): True for tasks that are a subtask of something,
                      False for the ones that are not. None sends no constraint.
        has_subtasks  (bool, optional): True for tasks that have subtasks, False for the
                      ones that do not. None sends no constraint.
        closed_by     (str, optional): Only tasks closed by a user, as subscriber. A task
                      that is still open was closed by nobody and never matches.
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
                closed_after,
                closed_before,
                ids,
                phids,
                subscriber,
                subtype,
                parent,
                subtask,
                # `is not None`, because False is the filter "tasks with no
                # parent" rather than the absence of one.
                has_parents is not None,
                has_subtasks is not None,
                closed_by,
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

        # The list-valued constraints, parsed before anything is fetched:
        # they cost no request, so a monogram that is not one is answered
        # before a search that would have thrown its answer away.
        # Named as the spec spells the key, which is also the flag without
        # its dashes: one value may arrive from either, so the sentence has
        # to make sense for both.
        task_ids = _task_id_list(ids, "'ids'")
        task_phids = _value_list(phids)
        subtypes = _value_list(subtype)
        parent_ids = _task_id_list(parent, "'parent'")
        subtask_ids = _task_id_list(subtask, "'subtask'")

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
        if closed_after:
            closed_after = days_ago_to_timestamp(parse_time_with_unit(closed_after))
        if closed_before:
            closed_before = days_ago_to_timestamp(parse_time_with_unit(closed_before))

        # Resolve the user filters - convert @me or username(s) to PHID(s)
        assigned_phids = self._resolve_user_filter_phids(
            assigned, "assigned to", option="--assigned"
        )
        author_phids = self._resolve_user_filter_phids(
            author, "authored by", option="--author"
        )
        subscriber_phids = self._resolve_user_filter_phids(
            subscriber, "subscribed to by", option="--subscriber"
        )
        closer_phids = self._resolve_user_filter_phids(
            closed_by, "closed by", option="--closed-by"
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

        # What the server can be asked instead of every task being fetched
        # and thrown away here. Each of these is worked out from a filter
        # that still runs in Python afterwards, so they change how much is
        # fetched and never what matches - see the three _lifted_* methods.
        # The column one needs the boards --tag resolved to, which is why
        # this is here and not beside the pattern parsing.
        lifted_statuses = self._lifted_statuses(status_patterns, status_scope)
        lifted_priorities = self._lifted_priorities(priority_patterns)
        lifted_column_phids = self._lifted_column_phids(
            column_patterns, project_phids if tag and tag != "*" else []
        )

        # Every code path below sends the same filters and differs only in
        # which projects it names, so the arguments are settled once.
        constraint_args = {
            "status_scope": status_scope,
            "statuses": lifted_statuses,
            "priorities": lifted_priorities,
            "column_phids": lifted_column_phids,
            "text_query": text_query,
            "assigned_phids": assigned_phids,
            "author_phids": author_phids,
            "subscriber_phids": subscriber_phids,
            "closer_phids": closer_phids,
            "space_phids": space_phids,
            "created_after": created_after,
            "created_before": created_before,
            "updated_after": updated_after,
            "updated_before": updated_before,
            "closed_after": closed_after,
            "closed_before": closed_before,
            "task_ids": task_ids,
            "task_phids": task_phids,
            "subtypes": subtypes,
            "parent_ids": parent_ids,
            "subtask_ids": subtask_ids,
            "has_parents": has_parents,
            "has_subtasks": has_subtasks,
        }

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
            constraints = self._build_search_constraints(**constraint_args)

            result_data = self._search_all_pages(constraints, order=api_order)
        else:
            base_constraints = self._build_search_constraints(**constraint_args)

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
            # Paged, because one maniphest.search answers with at most 100
            # tasks and a cursor: --include with more than a hundred ids
            # used to read the first page and silently drop the rest.
            included_tasks = search_all_pages(
                self._search_page,
                constraints={"ids": list(include_task_ids)},
                attachments={"columns": True},
            )

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
        `variables` and `tasks` (see docs/create-specs.md). It is read,
        never written - `Spec.from_data` deep-copies it - so the same dict
        can be handed in twice.

        Three steps with an inspectable object between them, all of which
        live in `phabfive.spec.create`: the data becomes a `Spec`, the spec
        becomes a `CreatePlan` with everything resolved and nothing sent,
        and the plan is applied. A program that wants the plan itself - to
        show it, to count it, to serialize it - calls `plan_create` and
        never this.

        Parameters
        ----------
        config : dict
            The template as data.
        dry_run : bool
            Preview rather than create. **Not** a parameter of applying: a
            dry run builds the plan and never applies it, so the preview
            cannot drift from what would be sent.

        Returns
        -------
        dict
            With "dry_run": True and the "tasks" that would be created, or
            the "task_ids" that were.

        Raises
        ------
        phabfive.spec.create.CreateFailed
            Something was refused partway through. `.report` holds one
            record per object, so the caller can say what exists now
            rather than only that it stopped.
        """
        from phabfive.spec.create import apply_spec, plan_create

        spec = self._create_spec(config)
        plan = plan_create(self, spec.render())

        if dry_run:
            # `depth + 1`, because the preview has always indented a
            # template's top-level tasks by one: the old recursion started
            # at the document itself, which creates nothing, and counted its
            # tasks as its children. `CreateItem.depth` is the honest 0 for
            # an item written at the top of its section.
            return {
                "dry_run": True,
                "tasks": [
                    {
                        "depth": item.depth + 1,
                        "title": item.display.get("title"),
                        "assignee": item.display.get("assignee"),
                        "subscribers": list(item.display.get("subscribers") or []),
                        "commits": list(item.display.get("commits") or []),
                    }
                    for item in plan.creating
                ],
            }

        # `raise_for_failure` and not "raise what the server said": Conduit
        # has no transactions, so a template whose fiftieth object is
        # refused has left forty-nine real objects behind, and an exception
        # carrying only the server's sentence throws away which. `CreateFailed`
        # carries the record per object - created, failed and skipped - and
        # the command prints them (#485).
        report = apply_spec(self, plan).raise_for_failure()

        # `task_ids` and not every id: a spec may create projects too, and a
        # project's id handed to `_show_tasks_after_write` would be looked
        # up as a task.
        return {"task_ids": report.task_ids}

    @staticmethod
    def _create_spec(config):
        """One creation template, as data, as a `Spec`.

        The three refusals a template has always answered with, kept here
        rather than in `phabfive.spec`: they name `tasks`, `variables` and
        "a creation template" the way the command's users have read them
        for years, where the envelope's own messages name a document and a
        spec. Everything past this point is the spec engine.
        """
        from phabfive.spec.envelope import Spec

        if not isinstance(config, dict):
            raise PhabfiveDataException(
                "A creation template is a mapping at the root level, not "
                f"{type(config).__name__}"
            )

        if "tasks" not in config:
            raise PhabfiveDataException(
                "Config file must contain keyword tasks in the root"
            )

        variables = config.get("variables")

        # A list or a scalar has no names to render with, and asking it for
        # .items() is the traceback the command has no handler for
        if variables is not None and not isinstance(variables, dict):
            raise PhabfiveConfigException(
                f"variables takes a mapping of name to value, not {variables!r}"
            )

        return Spec.from_data(config, kind="create")

    def create_task(
        self,
        title,
        description=None,
        tags=None,
        assignee=None,
        status=None,
        priority=None,
        subscribers=None,
        commits=None,
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
        commits : list, optional
            Commits to attach, by monogram, hash or PHID. Each item may hold
            several, separated by commas
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
        PhabfiveNotFoundException
            If a commit does not exist
        PhabfiveInputException
            If a commit hash matches more than one commit
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
        parsed_commits = split_list_option(commits)

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

        # Resolve commits to PHIDs (monograms, hashes or PHIDs)
        commit_display = []
        if parsed_commits:
            resolved_commits = resolve_commit_phids(
                self.phab, parsed_commits, option="--attach"
            )
            commit_phids = list(
                dict.fromkeys(phid for phid, _ in resolved_commits.values())
            )
            commit_display = list(
                dict.fromkeys(name for _, name in resolved_commits.values())
            )
            transactions.append({"type": "commits.set", "value": commit_phids})

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
                "commits": commit_display,
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
        attach=None,
        comment=None,
        space=None,
        visible_to=None,
        editable_by=None,
        unsubscribe=None,
        detach=None,
        unassign=False,
        tag=None,
        untag=None,
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
            Users to add as subscribers (@me for current user)
        attach : list, optional
            Commits to attach, by monogram, hash or PHID
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
        unsubscribe : list, optional
            Users to remove from the subscribers (@me for current user)
        detach : list, optional
            Commits to detach, spelled as for `attach`
        unassign : bool, optional
            Remove the assignee. Cannot be combined with `assign`.
        tag : list or dict, optional
            Projects to tag the task with, by name, hashtag, ID or PHID
            (repeatable, or comma-separated), or what `resolve_project_tags`
            returned for them, so a batch resolves them once
        untag : list or dict, optional
            Projects to remove the task from, given as for `tag`

        Returns
        -------
        tuple
            (transactions, changes) - the Conduit transactions to apply and a
            human-readable description of each one.

        Raises
        ------
        PhabfiveInputException
            On an argument value that cannot be used, a project both added
            and removed, or the board of `column` removed
        PhabfiveNotFoundException
            On a task, user, commit, project or column that does not exist
        PhabfiveConfigException
            If a policy value is outside the grammar
        PhabfiveDataException
            If a policy names a project or user that does not exist
        """
        validate_assignment(assign, unassign)

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

        added_tags = self._project_tags(tag, option="--tag")
        removed_tags = self._project_tags(untag, option="--untag")

        # A board found by auto-detection is never a --tag, so the refusal of
        # a project both added and removed does not see it: moving a task on a
        # board while taking it off that board is the same contradiction
        if column and board_phid:
            for value, (phid, name) in removed_tags.items():
                if phid == board_phid:
                    raise PhabfiveInputException(
                        f"Cannot both remove {name or value} and move the task "
                        f"to a column on it"
                    )

        # Handle column. Its transactions are kept aside until the tags are
        # known, because a task going onto a board goes through the same
        # projects.add as a --tag, and must be sent before the move.
        column_transactions = []
        column_changes = []
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
                # board and into a column is one edit. A board that is also
                # a --tag - the first one is how it is named - is already
                # being added, and joins the same transaction otherwise.
                task_projects = task_data["attachments"]["projects"]["projectPHIDs"]
                if board_phid not in task_projects and board_phid not in {
                    phid for phid, _ in added_tags.values()
                }:
                    added_tags = {
                        **added_tags,
                        board_phid: (board_phid, self._project_name(board_phid)),
                    }

                # A task already in the target column needs no transaction,
                # and reporting one would claim a move that never happened.
                if column_phid != current_col_phid:
                    column_transactions.append(
                        {"type": "column", "value": [column_phid]}
                    )

                    column_changes.append(
                        {
                            "field": "Column",
                            "old": current_column_name or "(none)",
                            "new": new_column_name,
                        }
                    )

        # Handle tags, as subscribers: only a change is sent, and the board a
        # --column puts the task on rides in the same projects.add
        if added_tags or removed_tags:
            tag_transactions, tag_changes = user_list_edit(
                "projects",
                "Tags",
                task_data.get("attachments", {})
                .get("projects", {})
                .get("projectPHIDs", []),
                added=added_tags,
                removed=removed_tags,
            )
            transactions.extend(tag_transactions)
            changes.extend(tag_changes)

        transactions.extend(column_transactions)
        changes.extend(column_changes)

        # Handle assignee
        if assign:
            [(user_phid, username)] = self._resolve_users(
                [assign], option="--assign"
            ).values()
            new_username = username or assign
            current_owner = task_data["fields"]["ownerPHID"]
            if user_phid != current_owner:
                transactions.append({"type": "owner", "value": user_phid})
                changes.append(
                    {
                        "field": "Assignee",
                        "old": self._owner_username(current_owner) or "(none)",
                        "new": new_username,
                    }
                )
        elif unassign:
            # A null owner is how maniphest.edit clears the assignee
            current_owner = task_data["fields"]["ownerPHID"]
            if current_owner:
                transactions.append({"type": "owner", "value": None})
                changes.append(
                    {
                        "field": "Assignee",
                        "old": self._owner_username(current_owner) or "(none)",
                        "new": "(none)",
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
        if subscribe or unsubscribe:
            sub_transactions, sub_changes = user_list_edit(
                "subscribers",
                "Subscribers",
                task_data.get("attachments", {})
                .get("subscribers", {})
                .get("subscriberPHIDs", []),
                added=self._resolve_users(
                    split_list_option(subscribe), option="--subscribe"
                ),
                removed=self._resolve_users(
                    split_list_option(unsubscribe), option="--unsubscribe"
                ),
            )
            transactions.extend(sub_transactions)
            changes.extend(sub_changes)

        # Handle commits, as subscribers: only a change is sent
        if attach or detach:
            commit_transactions, commit_changes = user_list_edit(
                "commits",
                "Commits",
                fetch_task_edges(self.phab, task_data["phid"], "commits"),
                added=resolve_commit_phids(
                    self.phab, split_list_option(attach), option="--attach"
                ),
                removed=resolve_commit_phids(
                    self.phab,
                    split_list_option(detach),
                    option="--detach",
                ),
            )
            transactions.extend(commit_transactions)
            changes.extend(commit_changes)

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

    def _project_tags(self, values, option=None):
        """The projects an edit adds or removes, as {value: (PHID, name)}.

        Already-resolved maps pass through, so a batch that resolved them once
        does not look every project up again for each task.
        """
        if isinstance(values, dict):
            return values
        return self._resolve_project_tags(split_list_option(values), option=option)

    def _project_name(self, phid):
        """A project's name for a preview, or None when it cannot be read."""
        for proj in fetch_projects_by_phid(self.phab, [phid]):
            return proj["fields"]["name"]
        return None

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
        unassign=False,
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
            unassign=unassign,
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

    def _owner_username(self, owner_phid):
        """The username of `owner_phid`, or None when there is none to show.

        Only labels a change, so a lookup that fails is not an error.
        """
        if not owner_phid:
            return None
        try:
            result = self.phab.user.search(constraints={"phids": [owner_phid]})
        except Exception:
            return None
        if result.get("data"):
            return result["data"][0]["fields"].get("username")
        return None

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
