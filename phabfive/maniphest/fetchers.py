# -*- coding: utf-8 -*-

"""API fetching functions for Maniphest operations."""

import logging

log = logging.getLogger(__name__)


def fetch_project_names_for_boards(phab, tasks_data):
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

    projects_lookup = phab.project.search(constraints={"phids": list(all_board_phids)})
    return {proj["phid"]: proj["fields"]["name"] for proj in projects_lookup["data"]}


def fetch_all_transactions(
    phab,
    task_phid,
    priority_map_func,
    need_columns=False,
    need_priority=False,
    need_status=False,
    need_assignee=False,
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
    phab : Phabricator
        Phabricator API client
    task_phid : str
        Task PHID (e.g., "PHID-TASK-...")
    priority_map_func : callable
        Function to get priority map (returns dict mapping values to names)
    need_columns : bool
        Whether to fetch and parse column transitions
    need_priority : bool
        Whether to fetch and parse priority transitions
    need_status : bool
        Whether to fetch and parse status transitions
    need_assignee : bool
        Whether to fetch and parse assignee transitions
    need_comments : bool
        Whether to fetch and parse comments

    Returns
    -------
    dict
        Dictionary with keys 'columns', 'priority', 'status', 'assignee', and 'comments',
        each containing a list of transaction dicts with keys:
        - oldValue: previous value (format depends on transaction type)
        - newValue: new value (format depends on transaction type)
        - dateCreated: timestamp (int)
        For comments: authorPHID, text, dateCreated
    """
    result_dict = {
        "columns": [],
        "priority": [],
        "status": [],
        "assignee": [],
        "comments": [],
    }

    # Early return if nothing requested
    if not (
        need_columns or need_priority or need_status or need_assignee or need_comments
    ):
        return result_dict

    try:
        # Extract task ID from PHID
        search_result = phab.maniphest.search(constraints={"phids": [task_phid]})

        if not search_result.get("data"):
            log.warning(f"No task found for PHID {task_phid}")
            return result_dict

        task_id = search_result["data"][0]["id"]

        # Single API call for all transaction types
        result = phab.maniphest.gettasktransactions(ids=[task_id])
        transactions = result.get(str(task_id), [])

        # Get priority map once if needed
        priority_map = None
        if need_priority:
            priority_map = priority_map_func()

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

            # Process assignee transitions
            elif need_assignee and trans_type in ["reassign", "core:owner"]:
                old_value = trans.get("oldValue")
                new_value = trans.get("newValue")

                transformed = {
                    "oldValue": old_value,  # User PHID or None
                    "newValue": new_value,  # User PHID or None
                    "dateCreated": int(trans.get("dateCreated", 0)),
                }
                result_dict["assignee"].append(transformed)

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
            f"{len(result_dict['assignee'])} assignee, "
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


def fetch_task_transactions(phab, task_phid):
    """
    Fetch transaction history for a task, filtered to column changes.

    Uses the deprecated but functional maniphest.gettasktransactions API
    because the modern transaction.search API doesn't properly expose
    column transitions.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
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
        search_result = phab.maniphest.search(constraints={"phids": [task_phid]})

        if not search_result.get("data"):
            log.warning(f"No task found for PHID {task_phid}")
            return []

        task_id = search_result["data"][0]["id"]

        # Use deprecated API that actually returns column transitions
        result = phab.maniphest.gettasktransactions(ids=[task_id])

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
                "oldValue": [board_phid, old_column_phid] if old_column_phid else None,
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


def fetch_priority_transactions(phab, task_phid, priority_map_func):
    """
    Fetch priority change transaction history for a task.

    Uses the deprecated but functional maniphest.gettasktransactions API.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    task_phid : str
        Task PHID (e.g., "PHID-TASK-...")
    priority_map_func : callable
        Function to get priority map

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
        search_result = phab.maniphest.search(constraints={"phids": [task_phid]})

        if not search_result.get("data"):
            log.warning(f"No task found for PHID {task_phid}")
            return []

        task_id = search_result["data"][0]["id"]

        # Use deprecated API that returns transaction history
        result = phab.maniphest.gettasktransactions(ids=[task_id])

        # Result is a dict keyed by task ID (as string)
        transactions = result.get(str(task_id), [])

        # Filter to only priority transactions
        priority_transactions = []
        priority_map = priority_map_func()

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


def get_api_priority_map():
    """
    Get mapping from Phabricator API numeric values to human-readable priority names.

    The Phabricator API returns numeric values (100, 90, 80, 50, 25, 0) which must
    be converted to display names ("Unbreak Now!", "Triage", "High", etc.).

    This is separate from PRIORITY_ORDER in priority_transitions.py, which is used
    for comparison/sorting (to determine if priority was raised or lowered).

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


def fetch_api_priority_values(phab):
    """This instance's priorities, as name and keyword to numeric value.

    `get_api_priority_map` is the *standard* table - 100/90/80/50/25/0 with
    Phabricator's own labels - and it is a constant, so it cannot answer
    what an instance that relabelled a priority calls it. Phorge ships
    `maniphest.priority.search`, which does, and this is that: the answer a
    caller needs before it may turn a priority **name** into the numeric
    value the `priorities` constraint takes.

    Both spellings are keyed, lowercased: the display name ("Needs Triage",
    which is what `fields.priority.name` carries and therefore what a
    client-side filter compares) and each keyword ("triage", which is what a
    task is edited with).

    **Every name is keyed before any keyword**, rather than record by
    record, so a keyword of one priority can never shadow the *name* of
    another. That is the one way this could give a wrong answer: the
    client-side filter compares display names, so a spelling that is one
    priority's name and another's keyword has to resolve to the priority
    whose name it is, or the narrowed fetch would return tasks the filter
    then drops.

    Raises rather than substituting the standard table. Narrowing a search
    by a value guessed from a table this instance does not use returns the
    wrong tasks and says nothing; the caller answers a failure by not
    narrowing at all.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client

    Returns
    -------
    dict
        Lowercased name or keyword to int value, e.g.
        ``{"high": 80, "needs triage": 90, "triage": 90}``
    """
    result = phab.maniphest.priority.search()

    records = [
        item
        for item in result.get("data", [])
        if isinstance(item.get("value"), int)
        and not isinstance(item.get("value"), bool)
    ]

    values = {}

    def remember(spelling, value):
        if isinstance(spelling, str) and spelling.strip():
            values.setdefault(spelling.strip().lower(), value)

    for record in records:
        remember(record.get("name"), record["value"])

    for record in records:
        for keyword in record.get("keywords") or []:
            remember(keyword, record["value"])

    log.debug(
        f"Fetched {len(values)} priority spellings from maniphest.priority.search"
    )

    return values


def get_api_priority_names(phab):
    """
    Get list of priority names from Phabricator API.

    Uses maniphest.priority.search to dynamically fetch all available
    priorities. This allows the tool to work with custom priority
    configurations in Phabricator/Phorge instances.

    Raises rather than substituting defaults, so that a caller which
    remembers the answer - shell completion caches it - cannot be handed an
    invented list to write down as though the server had said it. Its one
    caller falls back to DEFAULT_PRIORITY_VALUES itself.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client

    Returns
    -------
    list
        List of priority name strings (lowercase), e.g., ["unbreak", "high", "normal"]
    """
    result = phab.maniphest.priority.search()
    priorities = []
    for item in result.get("data", []):
        # maniphest.priority.search answers with flat records - name, keywords,
        # short, color, value - not the fields/attachments shape the *.search
        # endpoints use. The first keyword is the value tasks are edited with,
        # which is not derivable from the name: "Needs Triage" is "triage".
        keywords = item.get("keywords") or []
        if keywords:
            priorities.append(keywords[0])
    log.debug(f"Fetched {len(priorities)} priorities from maniphest.priority.search")
    return priorities


def fetch_api_status_map(phab):
    """
    Get status information from Phabricator API using maniphest.querystatuses.

    Raises rather than substituting defaults, so that a caller which
    remembers the answer - the command keeps it between runs - cannot be
    handed an invented map to write down as though the server had said it.
    get_api_status_map is the one that falls back.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client

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
    # A plain dict rather than the client's Result wrapper, which the cache
    # cannot serialise
    result = dict(phab.maniphest.querystatuses())
    log.debug(
        f"Fetched {len(result.get('allStatuses', []))} statuses from maniphest.querystatuses"
    )
    return result


def fallback_status_map():
    """The standard Phabricator statuses, for when the server cannot say.

    Never the server's answer, so never to be remembered as one.
    """
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


def get_api_status_map(phab):
    """
    Get status information from Phabricator API, or the standard statuses.

    Uses the maniphest.querystatuses API to dynamically fetch all available
    statuses and their metadata. This allows the tool to work with custom
    status configurations in Phabricator/Phorge instances.

    A failed lookup is answered with fallback_status_map(), because a
    command has to have a map to validate and display with. That makes what
    this returns unsafe to cache: use fetch_api_status_map for that.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client

    Returns
    -------
    dict
        The maniphest.querystatuses response, see fetch_api_status_map
    """
    try:
        return fetch_api_status_map(phab)
    except Exception as e:
        log.warning(f"Failed to fetch statuses from API: {e}. Using fallback statuses.")
        return fallback_status_map()


def get_column_info(phab, board_phid):
    """
    Get column information for a workboard.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    board_phid : str
        Project/board PHID

    Returns
    -------
    dict
        Mapping of column PHID to {"name": str, "sequence": int}
        Empty dict if board has no workboard or columns
    """
    column_info = {}

    try:
        # Use project.column.search to get all columns for the board
        result = phab.project.column.search(constraints={"projects": [board_phid]})

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


def get_current_column(task, board_phid, column_info):
    """
    Get the current column name for a task on a specific board.

    Parameters
    ----------
    task : dict
        Task data from maniphest.search
    board_phid : str
        Board PHID to check
    column_info : dict
        Column info mapping from get_column_info()

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

    col_data = column_info.get(column_phid)

    return col_data["name"] if col_data else None


def fetch_task_relationships(phab, task_phid, relationship_type):
    """
    Fetch parent or subtask relationships for a task.

    Uses the edge.search API to query task relationships.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    task_phid : str
        Task PHID (e.g., "PHID-TASK-...")
    relationship_type : str
        Either "parents" or "subtasks"

    Returns
    -------
    list
        List of related task PHIDs
    """
    edge_type = "task.parent" if relationship_type == "parents" else "task.subtask"

    try:
        result = phab.edge.search(sourcePHIDs=[task_phid], types=[edge_type])

        related_phids = []
        for edge in result.get("data", []):
            dest_phid = edge.get("destinationPHID")
            if dest_phid:
                related_phids.append(dest_phid)

        log.debug(
            f"Found {len(related_phids)} {relationship_type} for task {task_phid}"
        )
        return related_phids

    except Exception as e:
        log.warning(
            f"Failed to fetch {relationship_type} for {task_phid}: {type(e).__name__}: {e}"
        )
        return []
