# -*- coding: utf-8 -*-

"""Data formatting functions for Maniphest operations."""

import logging

from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.maniphest.fetchers import get_column_info
from phabfive.maniphest.utils import format_timestamp

log = logging.getLogger(__name__)


def build_priority_transitions(priority_transactions, format_direction_func):
    """
    Build priority transition history data for a task.

    Parameters
    ----------
    priority_transactions : list
        List of priority change transactions
    format_direction_func : callable
        Function to format direction arrows

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
        timestamp_str = format_timestamp(date_created) if date_created else "Unknown"

        # Handle case where there's no old value (initial priority set)
        old_priority_name = old_value if old_value else "(initial)"
        new_priority_name = new_value if new_value else "Unknown"

        # Determine if raised or lowered
        direction = f"[{format_direction_func('•')}]"
        if old_value and new_value:
            old_order = get_priority_order(old_value)
            new_order = get_priority_order(new_value)

            if old_order is not None and new_order is not None:
                if new_order < old_order:  # Raised (higher priority)
                    direction = f"[{format_direction_func('↑')}]"
                elif new_order > old_order:  # Lowered (lower priority)
                    direction = f"[{format_direction_func('↓')}]"

        arrow = format_direction_func("→")
        transitions.append(
            f"{timestamp_str} {direction} {old_priority_name} {arrow} {new_priority_name}"
        )

    return transitions


def build_comments(phab, comment_transactions, task_id):
    """
    Build comments list for a task in compact format.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
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
            result = phab.user.search(constraints={"phids": list(author_phids)})
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


def build_status_transitions(
    status_transactions, get_api_status_map_func, format_direction_func
):
    """
    Build status transition history data for a task.

    Parameters
    ----------
    status_transactions : list
        List of status change transactions
    get_api_status_map_func : callable
        Function to get API status map
    format_direction_func : callable
        Function to format direction arrows

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
        timestamp_str = format_timestamp(date_created) if date_created else "Unknown"

        # Handle case where there's no old value (initial status set)
        old_status_name = old_value if old_value else "(initial)"
        new_status_name = new_value if new_value else "Unknown"

        # Determine if raised (progressed) or lowered (regressed)
        direction = f"[{format_direction_func('•')}]"
        if old_value and new_value:
            # Get status info from API
            api_response = get_api_status_map_func()
            old_order = get_status_order(old_value, api_response)
            new_order = get_status_order(new_value, api_response)

            if old_order is not None and new_order is not None:
                if new_order > old_order:  # Raised (progressed forward)
                    direction = f"[{format_direction_func('↑')}]"
                elif new_order < old_order:  # Lowered (moved backward)
                    direction = f"[{format_direction_func('↓')}]"

        arrow = format_direction_func("→")
        transitions.append(
            f"{timestamp_str} {direction} {old_status_name} {arrow} {new_status_name}"
        )

    return transitions


def build_assignee_transitions(phab, assignee_transactions, format_direction_func):
    """
    Build assignee transition history data for a task.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    assignee_transactions : list
        List of assignee change transactions
    format_direction_func : callable
        Function to format direction arrows

    Returns
    -------
    list
        List of formatted transition strings
    """
    if not assignee_transactions:
        return []

    # Collect all user PHIDs that need resolution
    user_phids = set()
    for trans in assignee_transactions:
        if trans.get("oldValue"):
            user_phids.add(trans["oldValue"])
        if trans.get("newValue"):
            user_phids.add(trans["newValue"])

    # Resolve PHIDs to usernames
    user_map = {}
    if user_phids:
        result = phab.user.search(constraints={"phids": list(user_phids)})
        user_map = {u["phid"]: u["fields"]["username"] for u in result.get("data", [])}

    # Sort transitions chronologically (oldest first)
    sorted_transactions = sorted(
        assignee_transactions, key=lambda t: t.get("dateCreated", 0)
    )

    transitions = []
    for trans in sorted_transactions:
        old_value = trans.get("oldValue")
        new_value = trans.get("newValue")
        date_created = trans.get("dateCreated")

        # Format timestamp
        timestamp_str = format_timestamp(date_created) if date_created else "Unknown"

        # Resolve usernames
        old_name = user_map.get(old_value, "(none)") if old_value else "(none)"
        new_name = user_map.get(new_value, "(none)") if new_value else "(none)"

        direction = f"[{format_direction_func('•')}]"
        arrow = format_direction_func("→")
        transitions.append(f"{timestamp_str} {direction} {old_name} {arrow} {new_name}")

    return transitions


def build_column_transitions(transactions, column_info, format_direction_func):
    """
    Build transition history data for a task.

    Parameters
    ----------
    transitions : list
        List of column change transactions
    column_info : dict
        Mapping of column PHID to column info
    format_direction_func : callable
        Function to format direction arrows

    Returns
    -------
    list
        List of formatted transition strings
    """
    if not transactions:
        return []

    # Sort transitions chronologically (oldest first) for better readability
    sorted_transactions = sorted(transactions, key=lambda t: t.get("dateCreated", 0))

    transitions_list = []
    for trans in sorted_transactions:
        old_value = trans.get("oldValue")
        new_value = trans.get("newValue")
        date_created = trans.get("dateCreated")

        # Format timestamp
        timestamp_str = format_timestamp(date_created) if date_created else "Unknown"

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
        direction = f"[{format_direction_func('•')}]"
        if old_value and new_value and len(old_value) > 1 and len(new_value) > 1:
            old_seq = column_info.get(old_value[1], {}).get("sequence", 0)
            new_seq = column_info.get(new_value[1], {}).get("sequence", 0)
            if new_seq > old_seq:
                direction = f"[{format_direction_func('→')}]"
            elif new_seq < old_seq:
                direction = f"[{format_direction_func('←')}]"

        arrow = format_direction_func("→")
        transitions_list.append(
            f"{timestamp_str} {direction} {old_col_name} {arrow} {new_col_name}"
        )

    return transitions_list


def build_task_boards(boards, project_phid_to_name):
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
            column_data = columns[0]
            column_name = column_data.get("name", "Unknown")
            column_phid = column_data.get("phid", "")
            boards_dict[project_name] = {
                "Column": column_name,
                "_column_phid": column_phid,
            }

    return boards_dict


def build_history_section(
    phab,
    task_id,
    boards,
    project_phid_to_name,
    priority_transitions_map,
    task_transitions_map,
    status_transitions_map,
    get_api_status_map_func,
    format_direction_func,
    assignee_transitions_map=None,
):
    """
    Build History section dict with assignee, priority, status, and board transitions.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
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
    get_api_status_map_func : callable
        Function to get API status map
    format_direction_func : callable
        Function to format direction arrows
    assignee_transitions_map : dict, optional
        Mapping of task ID to assignee transitions

    Returns
    -------
    dict
        History section data
    """
    history = {}

    if assignee_transitions_map is None:
        assignee_transitions_map = {}

    # Build assignee transitions
    if task_id in assignee_transitions_map:
        assignee_trans = assignee_transitions_map[task_id]
        if assignee_trans:
            history["Assignee"] = build_assignee_transitions(
                phab, assignee_trans, format_direction_func
            )

    # Build priority transitions
    if task_id in priority_transitions_map:
        priority_trans = priority_transitions_map[task_id]
        if priority_trans:
            history["Priority"] = build_priority_transitions(
                priority_trans, format_direction_func
            )

    # Build status transitions
    if task_id in status_transitions_map:
        status_trans = status_transitions_map[task_id]
        if status_trans:
            history["Status"] = build_status_transitions(
                status_trans, get_api_status_map_func, format_direction_func
            )

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
                key=lambda item: project_phid_to_name.get(item[0], "Unknown").lower(),
            )

            for board_phid, board_transitions in sorted_transitions:
                project_name = project_phid_to_name.get(board_phid, "Unknown")
                column_info = get_column_info(phab, board_phid)
                transitions_list = build_column_transitions(
                    board_transitions, column_info, format_direction_func
                )
                boards_history[project_name] = transitions_list

            history["Boards"] = boards_history

    return history


def build_metadata_section(
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
            project_phid_to_name.get(phid, "Unknown") for phid in matching_board_phids
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


def build_task_display_data(
    phab,
    url,
    format_link_func,
    format_direction_func,
    get_api_status_map_func,
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
    """
    Build structured task display data from API results.

    This method is shared by both task_search() and task_show() commands
    to ensure consistent data structure.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    url : str
        Base URL for Phabricator instance
    format_link_func : callable
        Function to format hyperlinks
    format_direction_func : callable
        Function to format direction arrows
    get_api_status_map_func : callable
        Function to get API status map
    result_data : list
        List of task data from maniphest.search API
    task_transitions_map : dict, optional
        Mapping of task ID to column transitions
    priority_transitions_map : dict, optional
        Mapping of task ID to priority transitions
    status_transitions_map : dict, optional
        Mapping of task ID to status transitions
    assignee_transitions_map : dict, optional
        Mapping of task ID to assignee transitions
    comments_map : dict, optional
        Mapping of task ID to comments list
    matching_boards_map : dict, optional
        Mapping of task ID to matching board PHIDs
    matching_priority_map : dict, optional
        Mapping of task ID to priority match boolean
    matching_status_map : dict, optional
        Mapping of task ID to status match boolean
    show_history : bool, optional
        Whether to include transition history
    show_metadata : bool, optional
        Whether to include filter match metadata
    show_comments : bool, optional
        Whether to include comments

    Returns
    -------
    dict
        Dictionary with keys:
        - tasks: list of task display dicts
        - project_names: dict mapping PHID to project name
    """
    from phabfive.maniphest.fetchers import fetch_project_names_for_boards

    # Initialize empty dicts if None
    if task_transitions_map is None:
        task_transitions_map = {}
    if priority_transitions_map is None:
        priority_transitions_map = {}
    if status_transitions_map is None:
        status_transitions_map = {}
    if assignee_transitions_map is None:
        assignee_transitions_map = {}
    if comments_map is None:
        comments_map = {}
    if matching_boards_map is None:
        matching_boards_map = {}
    if matching_priority_map is None:
        matching_priority_map = {}
    if matching_status_map is None:
        matching_status_map = {}

    # Fetch project names for board display (always needed for nested format)
    project_phid_to_name = fetch_project_names_for_boards(phab, result_data)

    # Collect and resolve owner PHIDs to usernames
    owner_phids = set()
    for item in result_data:
        owner_phid = item.get("fields", {}).get("ownerPHID")
        if owner_phid:
            owner_phids.add(owner_phid)

    owner_map = {}
    if owner_phids:
        user_result = phab.user.search(constraints={"phids": list(owner_phids)})
        owner_map = {
            u["phid"]: u["fields"]["username"] for u in user_result.get("data", [])
        }

    # Build YAML data structure
    tasks_list = []

    for item in result_data:
        fields = item.get("fields", {})

        # Build task dict - store URL and formatted link separately
        task_url = f"{url}/T{item['id']}"
        link_text = f"T{item['id']}"
        task_dict = {
            "_url": task_url,
            "_link": format_link_func(task_url, link_text),
            "Task": {},
        }

        # Build task fields
        task_data = {}

        # Name
        task_data["Name"] = fields.get("name", "")

        # Status
        task_data["Status"] = fields.get("status", {}).get("name", "Unknown")

        # Priority
        task_data["Priority"] = fields.get("priority", {}).get("name", "Unknown")

        # Assignee - store separately for direct printing (hyperlink support)
        owner_phid = fields.get("ownerPHID")
        if owner_phid:
            username = owner_map.get(owner_phid, owner_phid)
            user_url = f"{url}/p/{username}/"
            task_dict["_assignee"] = format_link_func(
                user_url, username, show_url=False
            )
        else:
            task_dict["_assignee"] = "(none)"

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
        boards_data = build_task_boards(
            boards,
            project_phid_to_name,
        )
        if boards_data:
            task_dict["Boards"] = boards_data

        # Add Comments section if show_comments is enabled
        if show_comments:
            task_comments = comments_map.get(item["id"], [])
            if task_comments:
                comments_data = build_comments(phab, task_comments, item["id"])
                if comments_data:
                    task_dict["Comments"] = comments_data

        # Add History section if show_history is enabled
        if show_history:
            history_data = build_history_section(
                phab,
                item["id"],
                boards,
                project_phid_to_name,
                priority_transitions_map,
                task_transitions_map,
                status_transitions_map,
                get_api_status_map_func,
                format_direction_func,
                assignee_transitions_map,
            )
            if history_data:
                task_dict["History"] = history_data

        # Add Metadata section if show_metadata is enabled
        if show_metadata:
            metadata_data = build_metadata_section(
                item["id"],
                matching_boards_map,
                matching_priority_map,
                matching_status_map,
                project_phid_to_name,
            )
            task_dict["Metadata"] = metadata_data

        tasks_list.append(task_dict)

    return {
        "tasks": tasks_list,
        "project_names": project_phid_to_name,
    }
