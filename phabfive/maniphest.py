# -*- coding: utf-8 -*-

# python std lib
import datetime
import difflib
import fnmatch
import itertools
import json
import logging
import sys
import time
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Optional

from jinja2 import Environment, Template, meta

# 3rd party imports
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.constants import TICKET_PRIORITY_NORMAL

# phabfive imports
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveException,
    PhabfiveRemoteException,
)
from phabfive.project_filters import parse_project_patterns

# phabfive transition imports - imported in cli.py where patterns are parsed

log = logging.getLogger(__name__)


class Maniphest(Phabfive):
    def __init__(self):
        super(Maniphest, self).__init__()

    def _resolve_project_phids(self, project: str) -> list[str]:
        """
        Resolve project name, hashtag, or wildcard pattern to list of project PHIDs.

        Matches against all project slugs/hashtags, not just the primary name.
        This allows users to search using any hashtag associated with a project.

        Parameters
        ----------
        project (str): Project name, hashtag, or wildcard pattern.
                      Supports: "*" (all), "prefix*", "*suffix", "*contains*"
                      Matches against any project slug/hashtag (case-insensitive).

        Returns
        -------
        list: List of project PHIDs matching the pattern. Empty list if no matches.
              Duplicates are automatically removed when multiple slugs match the same project.
        """
        # Validate project parameter
        if not project or project == "":
            log.error("No project name provided. Use '*' to search all projects.")
            return []

        # Fetch all projects from Phabricator regardless of exact match or not to be able to suggest project names
        log.debug("Fetching all projects from Phabricator")

        # Use project.query to get slugs (project.search doesn't return all hashtags)
        # Note: project.query doesn't support pagination, so we fetch all at once
        slug_to_phid = {}  # Maps each slug/hashtag to its project PHID
        phid_to_primary_name = {}  # Maps PHID to primary project name

        try:
            # project.query returns a Result object with 'data' key containing projects
            projects_result = self.phab.project.query()
            projects_data = projects_result.get("data", {})

            # Process all projects (projects_data is a dict keyed by PHID)
            for phid, project_data in projects_data.items():
                primary_name = project_data["name"]
                phid_to_primary_name[phid] = primary_name

                # Always add the primary name as a searchable slug
                slug_to_phid[primary_name] = phid

                # Get all slugs (hashtags) for this project and add them too
                slugs = project_data.get("slugs", [])
                if slugs:
                    for slug in slugs:
                        if slug:
                            slug_to_phid[slug] = phid

        except Exception as e:
            log.error(f"Failed to fetch projects: {e}")
            return []

        log.debug(
            f"Fetched {len(phid_to_primary_name)} total projects with {len(slug_to_phid)} slugs/hashtags from Phabricator"
        )
        # Create case-insensitive lookup mappings for slugs
        lower_slug_to_phid = {slug.lower(): phid for slug, phid in slug_to_phid.items()}
        lower_slug_to_original = {slug.lower(): slug for slug in slug_to_phid.keys()}

        # Check if wildcard search is needed
        has_wildcard = "*" in project

        if has_wildcard:
            if project == "*":
                # Search all projects - return unique PHIDs
                unique_phids = list(set(slug_to_phid.values()))
                log.info(f"Wildcard '*' matched all {len(unique_phids)} projects")
                return unique_phids
            else:
                # Filter slugs by wildcard pattern (case-insensitive)
                # Use set to avoid duplicate PHIDs when multiple slugs of same project match
                matching_phids = set()
                matching_display_names = []

                for slug_lower in lower_slug_to_phid.keys():
                    if fnmatch.fnmatch(slug_lower, project.lower()):
                        phid = lower_slug_to_phid[slug_lower]
                        if phid not in matching_phids:
                            matching_phids.add(phid)
                            # Use primary name for display
                            matching_display_names.append(phid_to_primary_name[phid])

                if not matching_phids:
                    log.warning(f"Wildcard pattern '{project}' matched no projects")
                    return []

                log.info(
                    f"Wildcard pattern '{project}' matched {len(matching_phids)} "
                    + f"project(s): {', '.join(sorted(matching_display_names))}"
                )
                return list(matching_phids)
        # Exact match - validate project exists (case-insensitive)
        # Match against any slug/hashtag
        log.debug(f"Exact match mode, validating project '{project}'")

        project_lower = project.lower()
        if project_lower in lower_slug_to_phid:
            phid = lower_slug_to_phid[project_lower]
            matched_slug = lower_slug_to_original[project_lower]
            primary_name = phid_to_primary_name[phid]
            log.debug(
                f"Found case-insensitive match for project '{project}' -> slug '{matched_slug}' (primary: '{primary_name}')"
            )
            return [phid]
        else:
            # Project not found - suggest similar slugs (case-insensitive)
            # Deduplicate suggestions by PHID to avoid showing same project multiple times
            cutoff = 0.6 if len(project_lower) > 3 else 0.4
            similar_slugs = difflib.get_close_matches(
                project_lower, lower_slug_to_phid.keys(), n=10, cutoff=cutoff
            )

            if similar_slugs:
                # Deduplicate by PHID - show primary names with matched slugs
                seen_phids = set()
                unique_suggestions = []

                for slug in similar_slugs:
                    phid = lower_slug_to_phid[slug]
                    if phid not in seen_phids:
                        seen_phids.add(phid)
                        primary_name = phid_to_primary_name[phid]
                        original_slug = lower_slug_to_original[slug]

                        # Format: "Primary Name (matched-slug)"
                        unique_suggestions.append(f"{primary_name} ({original_slug})")

                # Limit to 3 unique projects
                unique_suggestions = unique_suggestions[:3]

                log.error(
                    f"Project '{project}' not found. Did you mean: {', '.join(unique_suggestions)}?"
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

    def _fetch_all_transactions(
        self,
        task_phid,
        need_columns=False,
        need_priority=False,
        need_status=False,
        need_comments=False,
    ):
        """
        Fetch all transaction types for a task in a single API call.

        This consolidates the transaction fetching to avoid redundant API calls
        when multiple transaction types are needed (e.g., when using --columns,
        --priority, and --status together).

        Uses the deprecated but functional maniphest.gettasktransactions API.

        Parameters
        ----------
        task_phid : str
            Task PHID (e.g., "PHID-TASK-...")
        need_columns : bool
            Whether to fetch and parse column transitions
        need_priority : bool
            Whether to fetch and parse priority transitions
        need_status : bool
            Whether to fetch and parse status transitions
        need_comments : bool
            Whether to fetch and parse comments

        Returns
        -------
        dict
            Dictionary with keys 'columns', 'priority', 'status', 'comments',
            each containing a list of transaction dicts with keys:
            - oldValue: previous value (format depends on transaction type)
            - newValue: new value (format depends on transaction type)
            - dateCreated: timestamp (int)
            For comments: authorPHID, text, dateCreated
        """
        result_dict = {"columns": [], "priority": [], "status": [], "comments": []}

        # Early return if nothing requested
        if not (need_columns or need_priority or need_status or need_comments):
            return result_dict

        try:
            # Extract task ID from PHID
            search_result = self.phab.maniphest.search(
                constraints={"phids": [task_phid]}
            )

            if not search_result.get("data"):
                log.warning(f"No task found for PHID {task_phid}")
                return result_dict

            task_id = search_result["data"][0]["id"]

            # Single API call for all transaction types
            result = self.phab.maniphest.gettasktransactions(ids=[task_id])
            transactions = result.get(str(task_id), [])

            # Get priority map once if needed
            priority_map = None
            if need_priority:
                priority_map = self._get_api_priority_map()

            # Process all transactions in a single pass
            for trans in transactions:
                trans_type = trans.get("transactionType")

                # Process column transitions
                if need_columns and trans_type == "core:columns":
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

                    old_column_phid = None
                    if from_columns:
                        old_column_phid = next(iter(from_columns.keys()), None)

                    transformed = {
                        "oldValue": [board_phid, old_column_phid]
                        if old_column_phid
                        else None,
                        "newValue": [board_phid, new_column_phid],
                        "dateCreated": int(trans.get("dateCreated", 0)),
                    }
                    result_dict["columns"].append(transformed)

                # Process priority transitions
                elif need_priority and trans_type in ["priority", "core:priority"]:
                    old_value = trans.get("oldValue")
                    new_value = trans.get("newValue")

                    # Resolve numeric priority values to names
                    old_value_resolved = None
                    if old_value is not None:
                        old_value_resolved = priority_map.get(old_value)
                        if old_value_resolved is None:
                            old_value_resolved = old_value

                    new_value_resolved = None
                    if new_value is not None:
                        new_value_resolved = priority_map.get(new_value)
                        if new_value_resolved is None:
                            new_value_resolved = new_value

                    transformed = {
                        "oldValue": old_value_resolved,
                        "newValue": new_value_resolved,
                        "dateCreated": int(trans.get("dateCreated", 0)),
                    }
                    result_dict["priority"].append(transformed)

                # Process status transitions
                elif need_status and trans_type in ["status", "core:status"]:
                    old_value = trans.get("oldValue")
                    new_value = trans.get("newValue")

                    transformed = {
                        "oldValue": old_value,
                        "newValue": new_value,
                        "dateCreated": int(trans.get("dateCreated", 0)),
                    }
                    result_dict["status"].append(transformed)

                # Process comments
                elif need_comments and trans_type == "core:comment":
                    comment_text = trans.get("comments", "")
                    if comment_text:  # Only add non-empty comments
                        transformed = {
                            "id": trans.get("transactionID"),
                            "authorPHID": trans.get("authorPHID"),
                            "text": comment_text,
                            "dateCreated": int(trans.get("dateCreated", 0)),
                        }
                        result_dict["comments"].append(transformed)

            log.debug(
                f"Fetched transactions for {task_phid} (T{task_id}): "
                f"{len(result_dict['columns'])} column, "
                f"{len(result_dict['priority'])} priority, "
                f"{len(result_dict['status'])} status, "
                f"{len(result_dict['comments'])} comments"
            )

            return result_dict

        except AttributeError as e:
            log.warning(
                f"Unexpected API response structure for {task_phid}: {e}. "
                "The Phabricator API format may have changed."
            )
            return result_dict
        except KeyError as e:
            log.warning(f"Missing expected data in API response for {task_phid}: {e}")
            return result_dict
        except Exception as e:
            log.warning(
                f"Failed to fetch transactions for {task_phid}: {type(e).__name__}: {e}"
            )
            return result_dict

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

    @lru_cache(maxsize=1)
    def _get_api_priority_map(self):
        """
        Get mapping from Phabricator API numeric values to human-readable priority names.

        The Phabricator API returns numeric values (100, 90, 80, 50, 25, 0) which must
        be converted to display names ("Unbreak Now!", "Triage", "High", etc.).

        This is separate from PRIORITY_ORDER in priority_transitions.py, which is used
        for comparison/sorting (to determine if priority was raised or lowered).

        Uses LRU cache (size=1) since priority mappings are global and rarely change.

        Returns
        -------
        dict
            Mapping of priority value (int/str) to priority name (str)
            Example: {100: "Unbreak Now!", 90: "Triage", 80: "High", ...}
        """
        # Standard Phabricator/Phorge API numeric priority values
        # Store both string and int versions for flexibility
        return {
            100: "Unbreak Now!",
            "100": "Unbreak Now!",
            90: "Triage",
            "90": "Triage",
            80: "High",
            "80": "High",
            50: "Normal",
            "50": "Normal",
            25: "Low",
            "25": "Low",
            0: "Wishlist",
            "0": "Wishlist",
        }

    @lru_cache(maxsize=1)
    def _get_api_status_map(self):
        """
        Get status information from Phabricator API using maniphest.querystatuses.

        Uses the maniphest.querystatuses API to dynamically fetch all available
        statuses and their metadata. This allows the tool to work with custom
        status configurations in Phabricator/Phorge instances.

        Uses LRU cache (size=1) since status mappings are global and rarely change.

        Returns
        -------
        dict
            Response from maniphest.querystatuses API containing:
            - defaultStatus: default status key
            - defaultClosedStatus: default closed status key
            - duplicateStatus: duplicate status key
            - openStatuses: list of open status keys
            - closedStatuses: dict of closed status keys/names
            - allStatuses: list of all status keys
            - statusMap: dict mapping status keys to display names
        """
        try:
            result = self.phab.maniphest.querystatuses()
            log.debug(
                f"Fetched {len(result.get('allStatuses', []))} statuses from maniphest.querystatuses"
            )
            return result
        except Exception as e:
            log.warning(
                f"Failed to fetch statuses from API: {e}. Using fallback statuses."
            )
            # Fallback to standard Phabricator statuses if API call fails
            return {
                "defaultStatus": "open",
                "defaultClosedStatus": "resolved",
                "duplicateStatus": "duplicate",
                "openStatuses": ["open"],
                "closedStatuses": {
                    "1": "resolved",
                    "2": "wontfix",
                    "3": "invalid",
                    "4": "duplicate",
                    "5": "spite",
                },
                "allStatuses": [
                    "open",
                    "resolved",
                    "wontfix",
                    "invalid",
                    "duplicate",
                    "spite",
                ],
                "statusMap": {
                    "open": "Open",
                    "resolved": "Resolved",
                    "wontfix": "Wontfix",
                    "invalid": "Invalid",
                    "duplicate": "Duplicate",
                    "spite": "Spite",
                },
            }

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

    def _fetch_priority_transactions(self, task_phid):
        """
        Fetch priority change transaction history for a task.

        Uses the deprecated but functional maniphest.gettasktransactions API.

        Parameters
        ----------
        task_phid : str
            Task PHID (e.g., "PHID-TASK-...")

        Returns
        -------
        list
            List of priority change transactions, each with keys:
            - oldValue: old priority name (str) or None
            - newValue: new priority name (str)
            - dateCreated: timestamp (int)
        """
        try:
            # Extract task ID from PHID
            search_result = self.phab.maniphest.search(
                constraints={"phids": [task_phid]}
            )

            if not search_result.get("data"):
                log.warning(f"No task found for PHID {task_phid}")
                return []

            task_id = search_result["data"][0]["id"]

            # Use deprecated API that returns transaction history
            result = self.phab.maniphest.gettasktransactions(ids=[task_id])

            # Result is a dict keyed by task ID (as string)
            transactions = result.get(str(task_id), [])

            # Filter to only priority transactions
            priority_transactions = []
            priority_map = self._get_api_priority_map()

            for trans in transactions:
                trans_type = trans.get("transactionType")

                # Priority changes can be in different transaction types
                if trans_type not in ["priority", "core:priority"]:
                    continue

                old_value = trans.get("oldValue")
                new_value = trans.get("newValue")

                # Resolve numeric priority values to human-readable names
                # Values can be int, str, or already a name
                old_value_resolved = None
                if old_value is not None:
                    # Try to resolve as numeric value first
                    old_value_resolved = priority_map.get(old_value)
                    # If not found in map, assume it's already a name
                    if old_value_resolved is None:
                        old_value_resolved = old_value

                new_value_resolved = None
                if new_value is not None:
                    new_value_resolved = priority_map.get(new_value)
                    if new_value_resolved is None:
                        new_value_resolved = new_value

                # Build transaction record with resolved names
                transformed = {
                    "oldValue": old_value_resolved,
                    "newValue": new_value_resolved,
                    "dateCreated": int(trans.get("dateCreated", 0)),
                }

                priority_transactions.append(transformed)

            log.debug(
                f"Found {len(priority_transactions)} priority transactions for {task_phid} (T{task_id})"
            )

            return priority_transactions
        except AttributeError as e:
            log.warning(
                f"Unexpected API response structure for {task_phid}: {e}. "
                "The Phabricator API format may have changed."
            )
            return []
        except KeyError as e:
            log.warning(f"Missing expected data in API response for {task_phid}: {e}")
            return []
        except Exception as e:
            log.warning(
                f"Failed to fetch priority transactions for {task_phid}: {type(e).__name__}: {e}"
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

    def _task_matches_priority_patterns(
        self, task, task_phid, priority_patterns, transactions=None
    ):
        """
        Check if a task matches any of the given priority patterns.

        Parameters
        ----------
        task : dict
            Task data from maniphest.search
        task_phid : str
            Task PHID
        priority_patterns : list
            List of PriorityPattern objects
        transactions : dict, optional
            Pre-fetched transactions dict with keys 'columns', 'priority', 'status'.
            If None, will fetch priority transactions using the old method.

        Returns
        -------
        tuple
            (matches: bool, priority_transactions: list)
            priority_transactions contains all priority change transactions
        """
        if not priority_patterns:
            return (True, [])  # No filtering needed

        # Use pre-fetched transactions if provided, otherwise fetch
        if transactions is not None and "priority" in transactions:
            priority_transactions = transactions["priority"]
        else:
            priority_transactions = self._fetch_priority_transactions(task_phid)

        # Get current priority
        current_priority = task.get("fields", {}).get("priority", {}).get("name")

        # Check if any pattern matches
        for pattern in priority_patterns:
            if pattern.matches(priority_transactions, current_priority):
                return (True, priority_transactions)

        return (False, [])

    def _task_matches_status_patterns(
        self, task, task_phid, status_patterns, transactions=None
    ):
        """
        Check if a task matches any of the given status patterns.

        Parameters
        ----------
        task : dict
            Task data from maniphest.search
        task_phid : str
            Task PHID
        status_patterns : list
            List of StatusPattern objects
        transactions : dict, optional
            Pre-fetched transactions dict with 'status' key.
            If not provided, will fetch status transactions.

        Returns
        -------
        tuple
            (matches: bool, status_transactions: list)
            status_transactions contains all status change transactions
        """
        if not status_patterns:
            return (True, [])  # No filtering needed

        # Use pre-fetched transactions if provided, otherwise fetch
        if transactions is not None and "status" in transactions:
            status_transactions = transactions["status"]
        else:
            # Fetch status transactions using consolidated method
            all_transactions = self._fetch_all_transactions(task_phid, need_status=True)
            status_transactions = all_transactions.get("status", [])

        # Get current status
        current_status = task.get("fields", {}).get("status", {}).get("name")

        # Check if any pattern matches
        for pattern in status_patterns:
            if pattern.matches(status_transactions, current_status):
                return (True, status_transactions)

        return (False, [])

    def _task_matches_project_patterns(
        self, task, project_patterns, resolved_phids_by_pattern
    ):
        """
        Check if a task matches the project filter criteria.

        For patterns with AND logic (multiple projects in one pattern):
        - Task must belong to ALL projects in at least one combination

        For patterns with OR logic (separate patterns from comma):
        - Task must belong to ANY project from ANY pattern

        Parameters
        ----------
        task : dict
            Task data from maniphest.search
        project_patterns : list
            List of ProjectPattern objects
        resolved_phids_by_pattern : list
            Either a list of PHIDs (for OR logic) or list of tuples (for AND logic)
            For 'dyn127*,dynatron': [['PHID-1', 'PHID-2', 'PHID-3'], ['PHID-4']]
            For 'dyn127*+dynatron': [[('PHID-1', 'PHID-4'), ('PHID-2', 'PHID-4'), ...]]

        Returns
        -------
        bool
            True if task matches the filter criteria, False otherwise
        """
        if not project_patterns or not resolved_phids_by_pattern:
            return True  # No filtering needed

        # Get project PHIDs from the boards the task is on
        # Project information is stored in attachments.columns.boards, not in fields
        boards = task.get("attachments", {}).get("columns", {}).get("boards", {})
        task_project_phids = set(boards.keys()) if boards else set()

        if not task_project_phids:
            # Task has no projects, can't match
            log.debug(f"Task {task.get('id')} has no projects")
            return False

        # Check each pattern (separated by comma = OR logic between patterns)
        for pattern, pattern_data in zip(project_patterns, resolved_phids_by_pattern):
            # If pattern has multiple project names (AND logic with +):
            # pattern_data is a list of tuples (combinations)
            if len(pattern.project_names) > 1:
                # AND logic: check if ANY combination matches
                for combo in pattern_data:
                    combo_set = set(combo)
                    if combo_set.issubset(task_project_phids):
                        return True
            else:
                # Single project in pattern (OR logic from wildcard expansion):
                # pattern_data is a flat list of PHIDs
                pattern_phids_set = set(pattern_data)
                if pattern_phids_set & task_project_phids:
                    return True

        return False

    def _task_matches_any_pattern(
        self, task, task_phid, patterns, board_phids, transactions=None
    ):
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
        transactions : dict, optional
            Pre-fetched transactions dict with keys 'columns', 'priority', 'status'.
            If None, will fetch column transactions using the old method.

        Returns
        -------
        tuple
            (matches: bool, all_transitions: list, matching_board_phids: set)
            all_transitions contains all transaction details for the task
            matching_board_phids contains PHIDs of boards that matched the pattern
        """
        if not patterns:
            return (True, [], set())  # No filtering needed

        # Use pre-fetched transactions if provided, otherwise fetch
        if transactions is not None and "columns" in transactions:
            column_transactions = transactions["columns"]
        else:
            column_transactions = self._fetch_task_transactions(task_phid)

        if not column_transactions and not any(
            any(cond.get("type") == "in" for cond in p.conditions) for p in patterns
        ):
            # No transactions and no in-only patterns
            return (False, [], set())

        # Track which boards matched the pattern
        matching_board_phids = set()

        # Check each board the task is on
        for board_phid in board_phids:
            column_info = self._get_column_info(board_phid)
            current_column = self._get_current_column(task, board_phid)

            # Filter transactions to this board
            board_transactions = [
                t
                for t in column_transactions
                if t.get("newValue")
                and len(t["newValue"]) > 0
                and t["newValue"][0] == board_phid
            ]

            # Check if any pattern matches
            for pattern in patterns:
                if pattern.matches(board_transactions, current_column, column_info):
                    matching_board_phids.add(board_phid)
                    break  # Board matched, no need to check other patterns for this board

        # Return match status, all transitions, and which boards matched
        if matching_board_phids:
            return (True, column_transactions, matching_board_phids)

        return (False, [], set())

    def _build_priority_transitions(self, priority_transactions):
        """
        Build priority transition history data for a task.

        Parameters
        ----------
        priority_transactions : list
            List of priority change transactions

        Returns
        -------
        list
            List of formatted transition strings
        """
        if not priority_transactions:
            return []

        from phabfive.priority_transitions import get_priority_order

        # Sort transitions chronologically (oldest first) for better readability
        sorted_transactions = sorted(
            priority_transactions, key=lambda t: t.get("dateCreated", 0)
        )

        transitions = []
        for trans in sorted_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")
            date_created = trans.get("dateCreated")

            # Format timestamp
            timestamp_str = (
                format_timestamp(date_created) if date_created else "Unknown"
            )

            # Handle case where there's no old value (initial priority set)
            old_priority_name = old_value if old_value else "(initial)"
            new_priority_name = new_value if new_value else "Unknown"

            # Determine if raised or lowered
            direction = "[•]"
            if old_value and new_value:
                old_order = get_priority_order(old_value)
                new_order = get_priority_order(new_value)

                if old_order is not None and new_order is not None:
                    if new_order < old_order:  # Raised (higher priority)
                        direction = "[↑]"
                    elif new_order > old_order:  # Lowered (lower priority)
                        direction = "[↓]"

            transitions.append(
                f"{timestamp_str} {direction} {old_priority_name} → {new_priority_name}"
            )

        return transitions

    def _build_comments(self, comment_transactions, task_id):
        """
        Build comments list for a task in compact format.

        Parameters
        ----------
        comment_transactions : list
            List of comment transactions with authorPHID, text, dateCreated
        task_id : int
            Task ID for building comment references (e.g., T5@93)

        Returns
        -------
        list
            List of formatted comment strings like:
            "2026-01-09T05:15:20 [admin T5@93] Comment text here..."
        """
        if not comment_transactions:
            return []

        # Collect all unique author PHIDs to resolve in a single API call
        author_phids = {
            t.get("authorPHID") for t in comment_transactions if t.get("authorPHID")
        }

        # Resolve author PHIDs to usernames
        author_map = {}
        if author_phids:
            try:
                result = self.phab.user.search(constraints={"phids": list(author_phids)})
                author_map = {
                    u["phid"]: u["fields"]["username"] for u in result.get("data", [])
                }
            except Exception as e:
                log.warning(f"Failed to resolve author PHIDs: {e}")

        # Sort comments chronologically (oldest first)
        sorted_comments = sorted(
            comment_transactions, key=lambda t: t.get("dateCreated", 0)
        )

        comments = []
        for trans in sorted_comments:
            comment_id = trans.get("id", "?")
            author_phid = trans.get("authorPHID")
            author_name = author_map.get(author_phid, author_phid or "Unknown")
            date_created = trans.get("dateCreated")
            text = trans.get("text", "")

            # Format timestamp
            timestamp_str = format_timestamp(date_created) if date_created else "Unknown"

            # Format: compact for single-line, block scalar for multi-line
            # Include comment reference (T5@93) for future edit/remove commands
            comment_ref = f"T{task_id}@{comment_id}"
            if "\n" in text:
                # Multi-line: use block scalar to preserve formatting
                # Put first line on same line as header
                lines = text.split("\n")
                header = f"{timestamp_str} [{author_name} {comment_ref}] {lines[0]}"
                remaining = "\n".join(lines[1:])
                full_text = f"{header}\n{remaining}"
                comments.append(PreservedScalarString(full_text))
            else:
                # Single-line: show full text
                comments.append(f"{timestamp_str} [{author_name} {comment_ref}] {text}")

        return comments

    def _build_status_transitions(self, status_transactions):
        """
        Build status transition history data for a task.

        Parameters
        ----------
        status_transactions : list
            List of status change transactions

        Returns
        -------
        list
            List of formatted transition strings
        """
        if not status_transactions:
            return []

        from phabfive.status_transitions import get_status_order

        # Sort transitions chronologically (oldest first) for better readability
        sorted_transactions = sorted(
            status_transactions, key=lambda t: t.get("dateCreated", 0)
        )

        transitions = []
        for trans in sorted_transactions:
            old_value = trans.get("oldValue")
            new_value = trans.get("newValue")
            date_created = trans.get("dateCreated")

            # Format timestamp
            timestamp_str = (
                format_timestamp(date_created) if date_created else "Unknown"
            )

            # Handle case where there's no old value (initial status set)
            old_status_name = old_value if old_value else "(initial)"
            new_status_name = new_value if new_value else "Unknown"

            # Determine if raised (progressed) or lowered (regressed)
            direction = "[•]"
            if old_value and new_value:
                # Get status info from API
                api_response = self._get_api_status_map()
                old_order = get_status_order(old_value, api_response)
                new_order = get_status_order(new_value, api_response)

                if old_order is not None and new_order is not None:
                    if new_order > old_order:  # Raised (progressed forward)
                        direction = "[↑]"
                    elif new_order < old_order:  # Lowered (moved backward)
                        direction = "[↓]"

            transitions.append(
                f"{timestamp_str} {direction} {old_status_name} → {new_status_name}"
            )

        return transitions

    def _build_column_transitions(self, transactions, column_info):
        """
        Build transition history data for a task.

        Parameters
        ----------
        transitions : list
            List of column change transactions
        column_info : dict
            Mapping of column PHID to column info

        Returns
        -------
        list
            List of formatted transition strings
        """
        if not transactions:
            return []

        # Sort transitions chronologically (oldest first) for better readability
        sorted_transactions = sorted(
            transactions, key=lambda t: t.get("dateCreated", 0)
        )

        transitions_list = []
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

            transitions_list.append(
                f"{timestamp_str} {direction} {old_col_name} → {new_col_name}"
            )

        return transitions_list

    def _build_task_boards(
        self,
        boards,
        project_phid_to_name,
    ):
        """
        Build board information dict with current columns only (no transitions).

        Parameters
        ----------
        boards : dict
            Board data from task attachments
        project_phid_to_name : dict
            Mapping of board PHID to project name

        Returns
        -------
        dict
            Dictionary mapping project names to board data
        """
        if isinstance(boards, list):
            log.debug(f"Boards is a list (likely empty): {boards}")
            return {}

        if not isinstance(boards, dict) or not boards:
            return {}

        boards_dict = {}

        # Sort boards alphabetically by project name
        sorted_boards = sorted(
            boards.items(),
            key=lambda item: project_phid_to_name.get(item[0], "Unknown").lower(),
        )

        for board_phid, board_data in sorted_boards:
            project_name = project_phid_to_name.get(board_phid, "Unknown")

            # Get current column only
            columns = board_data.get("columns", [])
            if columns:
                column_name = columns[0].get("name", "Unknown")
                boards_dict[project_name] = {"Column": column_name}

        return boards_dict

    def _build_history_section(
        self,
        task_id,
        boards,
        project_phid_to_name,
        priority_transitions_map,
        task_transitions_map,
        status_transitions_map,
    ):
        """
        Build History section dict with priority, status, and board transitions.

        Parameters
        ----------
        task_id : int
            Task ID
        boards : dict
            Board data from task attachments
        project_phid_to_name : dict
            Mapping of board PHID to project name
        priority_transitions_map : dict
            Mapping of task ID to priority transitions
        task_transitions_map : dict
            Mapping of task ID to board transitions
        status_transitions_map : dict
            Mapping of task ID to status transitions

        Returns
        -------
        dict
            History section data
        """
        history = {}

        # Build priority transitions
        if task_id in priority_transitions_map:
            priority_trans = priority_transitions_map[task_id]
            if priority_trans:
                history["Priority"] = self._build_priority_transitions(priority_trans)

        # Build status transitions
        if task_id in status_transitions_map:
            status_trans = status_transitions_map[task_id]
            if status_trans:
                history["Status"] = self._build_status_transitions(status_trans)

        # Build board transitions
        if task_id in task_transitions_map:
            all_transitions = task_transitions_map[task_id]

            # Group transitions by board
            transitions_by_board = {}
            for trans in all_transitions:
                if trans.get("newValue") and len(trans["newValue"]) > 0:
                    board_phid = trans["newValue"][0]
                    if board_phid not in transitions_by_board:
                        transitions_by_board[board_phid] = []
                    transitions_by_board[board_phid].append(trans)

            if transitions_by_board:
                boards_history = {}

                # Sort boards alphabetically by project name
                sorted_transitions = sorted(
                    transitions_by_board.items(),
                    key=lambda item: project_phid_to_name.get(
                        item[0], "Unknown"
                    ).lower(),
                )

                for board_phid, board_transitions in sorted_transitions:
                    project_name = project_phid_to_name.get(board_phid, "Unknown")
                    column_info = self._get_column_info(board_phid)
                    transitions_list = self._build_column_transitions(
                        board_transitions, column_info
                    )
                    boards_history[project_name] = transitions_list

                history["Boards"] = boards_history

        return history

    def _build_metadata_section(
        self,
        task_id,
        matching_boards_map,
        matching_priority_map,
        matching_status_map,
        project_phid_to_name,
    ):
        """
        Build Metadata section dict with filter match information.

        Parameters
        ----------
        task_id : int
            Task ID
        matching_boards_map : dict
            Mapping of task ID to set of matching board PHIDs
        matching_priority_map : dict
            Mapping of task ID to priority match boolean
        matching_status_map : dict
            Mapping of task ID to status match boolean
        project_phid_to_name : dict
            Mapping of board PHID to project name

        Returns
        -------
        dict
            Metadata section data
        """
        metadata = {}

        # Build matched boards
        if task_id in matching_boards_map:
            matching_board_phids = matching_boards_map[task_id]
            board_names = [
                project_phid_to_name.get(phid, "Unknown")
                for phid in matching_board_phids
            ]
            metadata["MatchedBoards"] = board_names
        else:
            metadata["MatchedBoards"] = []

        # Build matched priority
        if task_id in matching_priority_map:
            matched_priority = matching_priority_map[task_id]
            metadata["MatchedPriority"] = matched_priority
        else:
            metadata["MatchedPriority"] = False

        # Build matched status
        if task_id in matching_status_map:
            matched_status = matching_status_map[task_id]
            metadata["MatchedStatus"] = matched_status
        else:
            metadata["MatchedStatus"] = False

        return metadata

    def _format_and_display_tasks(
        self,
        result_data,
        task_transitions_map=None,
        priority_transitions_map=None,
        status_transitions_map=None,
        comments_map=None,
        matching_boards_map=None,
        matching_priority_map=None,
        matching_status_map=None,
        show_history=False,
        show_metadata=False,
        show_comments=False,
    ):
        """
        Format and display tasks in YAML format.

        This method is shared by both task_search() and task_show() commands
        to ensure consistent output formatting.

        Parameters
        ----------
        result_data : list
            List of task data from maniphest.search API
        task_transitions_map : dict, optional
            Mapping of task ID to column transitions
        priority_transitions_map : dict, optional
            Mapping of task ID to priority transitions
        status_transitions_map : dict, optional
            Mapping of task ID to status transitions
        comments_map : dict, optional
            Mapping of task ID to comments list
        matching_boards_map : dict, optional
            Mapping of task ID to matching board PHIDs
        matching_priority_map : dict, optional
            Mapping of task ID to priority match boolean
        matching_status_map : dict, optional
            Mapping of task ID to status match boolean
        show_history : bool, optional
            Whether to display transition history
        show_metadata : bool, optional
            Whether to display filter match metadata
        show_comments : bool, optional
            Whether to display comments
        """
        # Initialize empty dicts if None
        if task_transitions_map is None:
            task_transitions_map = {}
        if priority_transitions_map is None:
            priority_transitions_map = {}
        if status_transitions_map is None:
            status_transitions_map = {}
        if comments_map is None:
            comments_map = {}
        if matching_boards_map is None:
            matching_boards_map = {}
        if matching_priority_map is None:
            matching_priority_map = {}
        if matching_status_map is None:
            matching_status_map = {}

        # Fetch project names for board display (always needed for nested format)
        project_phid_to_name = self._fetch_project_names_for_boards(result_data)

        # Build YAML data structure
        tasks_list = []

        for item in result_data:
            fields = item.get("fields", {})

            # Build task dict
            task_dict = {"Link": f"{self.url}/T{item['id']}", "Task": {}}

            # Build task fields
            task_data = {}

            # Name
            task_data["Name"] = fields.get("name", "")

            # Status
            task_data["Status"] = fields.get("status", {}).get("name", "Unknown")

            # Priority
            task_data["Priority"] = fields.get("priority", {}).get("name", "Unknown")

            # Dates
            if fields.get("dateCreated"):
                task_data["Created"] = format_timestamp(fields["dateCreated"])
            if fields.get("dateModified"):
                task_data["Modified"] = format_timestamp(fields["dateModified"])
            if fields.get("dateClosed"):
                task_data["Closed"] = format_timestamp(fields["dateClosed"])

            # Description - use PreservedScalarString for multi-line descriptions
            description_raw = fields.get("description", {}).get("raw", "")
            if description_raw and "\n" in description_raw:
                task_data["Description"] = PreservedScalarString(description_raw)
            else:
                task_data["Description"] = description_raw if description_raw else ""

            task_dict["Task"] = task_data

            # Display board information (current columns only)
            columns_data = item.get("attachments", {}).get("columns", {})
            log.debug(f"Full columns data structure: {columns_data}")
            boards = columns_data.get("boards", {})
            log.debug(f"Boards type: {type(boards)}, value: {boards}")
            boards_data = self._build_task_boards(
                boards,
                project_phid_to_name,
            )
            if boards_data:
                task_dict["Boards"] = boards_data

            # Add Comments section if show_comments is enabled
            if show_comments:
                task_comments = comments_map.get(item["id"], [])
                if task_comments:
                    comments_data = self._build_comments(task_comments, item["id"])
                    if comments_data:
                        task_dict["Comments"] = comments_data

            # Add History section if show_history is enabled
            if show_history:
                history_data = self._build_history_section(
                    item["id"],
                    boards,
                    project_phid_to_name,
                    priority_transitions_map,
                    task_transitions_map,
                    status_transitions_map,
                )
                if history_data:
                    task_dict["History"] = history_data

            # Add Metadata section if show_metadata is enabled
            if show_metadata:
                metadata_data = self._build_metadata_section(
                    item["id"],
                    matching_boards_map,
                    matching_priority_map,
                    matching_status_map,
                    project_phid_to_name,
                )
                task_dict["Metadata"] = metadata_data

            tasks_list.append(task_dict)

        # Output as YAML using ruamel.yaml for proper multi-line formatting
        print()  # Empty line for separation

        yaml = YAML()
        yaml.default_flow_style = False
        yaml.preserve_quotes = True
        yaml.width = 4096  # Avoid unwanted line wrapping
        yaml.dump(tasks_list, sys.stdout)

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

        # Initialize maps for storing transitions and comments
        task_transitions_map = {}
        priority_transitions_map = {}
        status_transitions_map = {}
        comments_map = {}

        # Fetch transaction data if any transaction-based info is requested
        if show_history or show_comments:
            task_phid = result_data[0].get("phid")
            if task_phid:
                log.info(f"Fetching transaction data for T{task_id}")
                # Fetch all transaction types in a single API call
                all_fetched_transactions = self._fetch_all_transactions(
                    task_phid,
                    need_columns=show_history,
                    need_priority=show_history,
                    need_status=show_history,
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
                if all_fetched_transactions.get("comments"):
                    comments_map[task_id] = all_fetched_transactions["comments"]

        # Use shared method to format and display the task
        self._format_and_display_tasks(
            result_data,
            task_transitions_map=task_transitions_map,
            priority_transitions_map=priority_transitions_map,
            status_transitions_map=status_transitions_map,
            comments_map=comments_map,
            show_history=show_history,
            show_metadata=show_metadata,
            show_comments=show_comments,
        )

    def _load_search_from_yaml(self, template_path):
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
            with open(template_file, 'r', encoding='utf-8') as f:
                yaml_loader = YAML()
                # Load all documents from the YAML file
                documents = list(yaml_loader.load_all(f))
        except Exception as e:
            raise PhabfiveException(f"Failed to parse template file {template_path}: {e}")

        if not documents:
            raise PhabfiveException("Template file contains no documents")

        search_configs = []
        supported_params = {
            'text_query', 'tag', 'created-after', 'updated-after',
            'column', 'priority', 'status', 'show-history', 'show-metadata'
        }

        for i, data in enumerate(documents):
            if not isinstance(data, dict):
                raise PhabfiveException(
                    f"Document {i + 1} in YAML file must contain a dictionary at root level"
                )

            search_params = data.get('search', {})
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
                'search': search_params,
                'title': data.get('title', f"Search {i + 1}"),
                'description': data.get('description', None)
            }
            search_configs.append(config)

        log.info(f"Loaded {len(search_configs)} search configuration(s) from {template_path}")
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
            raise PhabfiveConfigException(
                "No search criteria specified. Please provide at least one of:\n"
                "  - Free-text query: phabfive maniphest search 'search text'\n"
                "  - Project tag: --tag='Project Name'\n"
                "  - Date filter: --created-after=N or --updated-after=N\n"
                "  - Column filter: --column='pattern'\n"
                "  - Priority filter: --priority='pattern'\n"
                "  - Status filter: --status='pattern'"
            )

        # Convert date filters to Unix timestamps
        if created_after:
            created_after = days_to_unix(created_after)
        if updated_after:
            updated_after = days_to_unix(updated_after)

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

                    log.info(
                        f"Tag pattern '{tag}' resolved to {len(project_phids)} project(s)"
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
            # Handle multiple projects with OR logic (make separate calls and merge)
            if len(project_phids) > 1:
                log.info(f"Searching {len(project_phids)} projects with OR logic")
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
            if transition_patterns:
                filter_desc.append("column transition patterns")
            if priority_patterns:
                filter_desc.append("priority patterns")
            if status_patterns:
                filter_desc.append("status patterns")
            if project_patterns:
                filter_desc.append("project patterns")
            log.info(
                f"Filtering {len(result_data)} tasks by {' and '.join(filter_desc)}"
            )

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

            log.info(f"Filtered down to {len(filtered_tasks)} tasks matching patterns")
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

        # Use shared method to format and display tasks
        self._format_and_display_tasks(
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

    def _display_task_transitions(self, task_phid):
        """
        Fetch and display transition history for a single task.

        Parameters
        ----------
        task_phid : str
            Task PHID (e.g., "PHID-TASK-...")
        """
        # Fetch column transitions using consolidated method
        all_transactions = self._fetch_all_transactions(
            task_phid, need_columns=True, need_priority=False
        )
        transactions = all_transactions.get("columns", [])

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
                transitions_list = self._build_column_transitions(
                    board_transactions, column_info
                )
                for transition in transitions_list:
                    print(f"      - {transition}")

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

    def create_from_config(self, create_config, dry_run=False):
        if not create_config:
            raise PhabfiveException("Must specify a config file path")

        if not Path(create_config).is_file():
            log.error(f"Config file '{create_config}' do not exists")
            return

        with open(create_config) as stream:
            yaml_loader = YAML()
            root_data = yaml_loader.load(stream)

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
            child_tasks = task_config.get("tasks", None)

            if not transactions_to_commit and not child_tasks:
                log.warning(
                    "No transactions to commit and no child tasks - possible data issue"
                )

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
