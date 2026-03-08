# -*- coding: utf-8 -*-
"""Display functions for Maniphest tasks."""

import json
import sys
from io import StringIO

from rich.markup import escape
from rich.text import Text
from rich.tree import Tree
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import PreservedScalarString


def _escape_for_rich(content):
    """Escape user content for safe Rich printing.

    Rich interprets square brackets [...] as markup syntax.
    This causes issues with user content containing:
    - [/path] patterns -> MarkupError crash (interpreted as closing tag)
    - [bug] tags -> Silently stripped (interpreted as invalid style)

    Parameters
    ----------
    content : any
        User-provided content to escape

    Returns
    -------
    str or Text
        Empty string for None, Text objects unchanged, escaped string otherwise
    """
    if content is None:
        return ""
    if isinstance(content, Text):
        # Text objects are already safe Rich objects (e.g., hyperlinks)
        return content
    return escape(str(content))


def _needs_yaml_quoting(value):
    """Check if a string value needs YAML quoting.

    Values need quoting if they contain YAML special characters
    that could be misinterpreted.
    """
    if not isinstance(value, str):
        return False
    # YAML special chars: colon, braces, brackets, backticks, quotes, empty string
    return value == "" or any(c in value for c in ":{}[]`'\"")


def display_task_rich(console, task_dict, phabfive_instance):
    """Display a single task in YAML-like format using Rich.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    task_dict : dict
        Task data dictionary with _link, _url, _assignee, Task, Boards, etc.
    phabfive_instance : Phabfive
        Instance to access format_link() and url
    """
    # Extract internal fields
    link = task_dict.get("_link")
    assignee = task_dict.get("_assignee")
    space = task_dict.get("_space")
    task_data = task_dict.get("Task", {})
    boards = task_dict.get("Boards", {})
    parents = task_dict.get("Parents", [])
    subtasks = task_dict.get("Subtasks", [])
    history = task_dict.get("History", {})
    metadata = task_dict.get("Metadata", {})

    # Print link
    console.print(Text.assemble("- Link: ", link))

    # Print Task section
    console.print("  Task:")
    for key, value in task_data.items():
        # Check line width before printing
        phabfive_instance.check_line_width(value, f"Task.{key}")

        if isinstance(value, (str, PreservedScalarString)) and "\n" in str(value):
            # Multi-line value
            console.print(f"    {key}: |-")
            for line in str(value).splitlines():
                console.print(f"      {_escape_for_rich(line)}")
        elif _needs_yaml_quoting(value):
            escaped = str(value).replace("'", "''")
            console.print(f"    {key}: '{_escape_for_rich(escaped)}'")
        else:
            console.print(f"    {key}: {_escape_for_rich(value)}")

    # Print Assignee
    if assignee:
        console.print(Text.assemble("    Assignee: ", assignee))

    # Print Space with clickable link
    if space:
        console.print(Text.assemble("    Space: ", space))

    # Print Boards compacted: "Board-Name: Column-Value"
    if boards:
        console.print("  Boards:")
        for board_name, board_data in boards.items():
            project_slug = board_name.lower().replace(" ", "-")
            board_url = f"{phabfive_instance.url}/tag/{project_slug}/"
            board_link = phabfive_instance.format_link(
                board_url, board_name, show_url=False
            )

            if isinstance(board_data, dict):
                column_value = board_data.get("Column", "")
                column_phid = board_data.get("_column_phid", "")
                if column_phid:
                    query_url = f"{phabfive_instance.url}/maniphest/?columns={column_phid}&statuses=open()"
                    column_link = phabfive_instance.format_link(
                        query_url, column_value, show_url=False
                    )
                    console.print(Text.assemble("    ", board_link, ": ", column_link))
                else:
                    console.print(
                        Text.assemble(
                            "    ", board_link, f": {_escape_for_rich(column_value)}"
                        )
                    )

    # Print Parents section (only if non-empty)
    if parents:
        console.print("  Parents:")
        for parent in parents:
            # Format: {"Link": "url", "Task": {"Name": "title"}}
            link = parent.get("Link", "")
            name = parent.get("Task", {}).get("Name", "")
            task_id = link.split("/")[-1] if link else ""
            task_link = phabfive_instance.format_link(link, task_id, show_url=False)
            console.print(
                Text.assemble("    - ", task_link, f": {_escape_for_rich(name)}")
            )

    # Print Subtasks section (only if non-empty)
    if subtasks:
        console.print("  Subtasks:")
        for subtask in subtasks:
            # Format: {"Link": "url", "Task": {"Name": "title"}}
            link = subtask.get("Link", "")
            name = subtask.get("Task", {}).get("Name", "")
            task_id = link.split("/")[-1] if link else ""
            task_link = phabfive_instance.format_link(link, task_id, show_url=False)
            console.print(
                Text.assemble("    - ", task_link, f": {_escape_for_rich(name)}")
            )

    # Print History section
    if history:
        console.print("  History:")
        for hist_key, hist_value in history.items():
            if hist_key == "Boards" and isinstance(hist_value, dict):
                console.print("    Boards:")
                for board_name, transitions in hist_value.items():
                    console.print(f"      {_escape_for_rich(board_name)}:")
                    for trans in transitions:
                        console.print(f"        - {_escape_for_rich(trans)}")
            elif isinstance(hist_value, list):
                console.print(f"    {hist_key}:")
                for trans in hist_value:
                    console.print(f"      - {_escape_for_rich(trans)}")

    # Print Comments section
    comments = task_dict.get("Comments", [])
    if comments:
        console.print("  Comments:")
        for comment in comments:
            if isinstance(comment, PreservedScalarString) or "\n" in str(comment):
                # Multi-line comment
                lines = str(comment).splitlines()
                console.print(f"    - {_escape_for_rich(lines[0])}")
                for line in lines[1:]:
                    console.print(f"      {_escape_for_rich(line)}")
            else:
                console.print(f"    - {_escape_for_rich(comment)}")

    # Print Metadata section
    if metadata:
        console.print("  Metadata:")
        for meta_key, meta_value in metadata.items():
            if isinstance(meta_value, list):
                if meta_value:
                    console.print(f"    {meta_key}:")
                    for item in meta_value:
                        console.print(f"      - {_escape_for_rich(item)}")
                else:
                    console.print(f"    {meta_key}: []")
            else:
                console.print(f"    {meta_key}: {_escape_for_rich(meta_value)}")


def display_task_tree(console, task_dict, phabfive_instance):
    """Display a single task in tree format using Rich Tree.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    task_dict : dict
        Task data dictionary with _link, _url, _assignee, Task, Boards, etc.
    phabfive_instance : Phabfive
        Instance to access format_link() and url
    """
    # Extract internal fields
    link = task_dict.get("_link")
    assignee = task_dict.get("_assignee")
    space = task_dict.get("_space")
    task_data = task_dict.get("Task", {})
    boards = task_dict.get("Boards", {})
    parents = task_dict.get("Parents", [])
    subtasks = task_dict.get("Subtasks", [])
    history = task_dict.get("History", {})
    metadata = task_dict.get("Metadata", {})

    # Create tree with task link as root
    tree = Tree(link)

    # Add Task section
    task_branch = tree.add("Task")
    for key, value in task_data.items():
        if isinstance(value, (str, PreservedScalarString)) and "\n" in str(value):
            # Truncate multi-line descriptions in tree view
            first_line = str(value).split("\n")[0]
            if len(first_line) > 60:
                first_line = first_line[:57] + "..."
            task_branch.add(f"{key}: {_escape_for_rich(first_line)}")
        else:
            task_branch.add(f"{key}: {_escape_for_rich(value)}")

    # Add Assignee
    if assignee:
        task_branch.add(Text.assemble("Assignee: ", assignee))

    # Add Space
    if space:
        task_branch.add(Text.assemble("Space: ", space))

    # Add Boards section compacted: "Board-Name: Column-Value"
    if boards:
        boards_branch = tree.add("Boards")
        for board_name, board_data in boards.items():
            project_slug = board_name.lower().replace(" ", "-")
            board_url = f"{phabfive_instance.url}/tag/{project_slug}/"
            board_link = phabfive_instance.format_link(
                board_url, board_name, show_url=False
            )

            if isinstance(board_data, dict):
                column_value = board_data.get("Column", "")
                column_phid = board_data.get("_column_phid", "")
                if column_phid:
                    query_url = f"{phabfive_instance.url}/maniphest/?columns={column_phid}&statuses=open()"
                    column_link = phabfive_instance.format_link(
                        query_url, column_value, show_url=False
                    )
                    boards_branch.add(Text.assemble(board_link, ": ", column_link))
                else:
                    boards_branch.add(
                        Text.assemble(board_link, f": {_escape_for_rich(column_value)}")
                    )

    # Add Parents section (only if non-empty)
    if parents:
        parents_branch = tree.add("Parents")
        for parent in parents:
            # Format: {"Link": "url", "Task": {"Name": "title"}}
            link = parent.get("Link", "")
            name = parent.get("Task", {}).get("Name", "")
            task_id = link.split("/")[-1] if link else ""
            task_link = phabfive_instance.format_link(link, task_id, show_url=False)
            parents_branch.add(Text.assemble(task_link, f": {_escape_for_rich(name)}"))

    # Add Subtasks section (only if non-empty)
    if subtasks:
        subtasks_branch = tree.add("Subtasks")
        for subtask in subtasks:
            # Format: {"Link": "url", "Task": {"Name": "title"}}
            link = subtask.get("Link", "")
            name = subtask.get("Task", {}).get("Name", "")
            task_id = link.split("/")[-1] if link else ""
            task_link = phabfive_instance.format_link(link, task_id, show_url=False)
            subtasks_branch.add(Text.assemble(task_link, f": {_escape_for_rich(name)}"))

    # Add History section
    if history:
        history_branch = tree.add("History")
        for hist_key, hist_value in history.items():
            if hist_key == "Boards" and isinstance(hist_value, dict):
                boards_hist = history_branch.add("Boards")
                for board_name, transitions in hist_value.items():
                    board_hist = boards_hist.add(_escape_for_rich(board_name))
                    for trans in transitions:
                        board_hist.add(_escape_for_rich(trans))
            elif isinstance(hist_value, list):
                hist_type_branch = history_branch.add(hist_key)
                for trans in hist_value:
                    hist_type_branch.add(_escape_for_rich(trans))

    # Add Comments section
    comments = task_dict.get("Comments", [])
    if comments:
        comments_branch = tree.add("Comments")
        for comment in comments:
            if isinstance(comment, PreservedScalarString) or "\n" in str(comment):
                # Truncate multi-line comments in tree view
                first_line = str(comment).split("\n")[0]
                if len(first_line) > 60:
                    first_line = first_line[:57] + "..."
                comments_branch.add(_escape_for_rich(first_line))
            else:
                comments_branch.add(_escape_for_rich(str(comment)))

    # Add Metadata section
    if metadata:
        meta_branch = tree.add("Metadata")
        for meta_key, meta_value in metadata.items():
            if isinstance(meta_value, list):
                if meta_value:
                    list_branch = meta_branch.add(meta_key)
                    for item in meta_value:
                        list_branch.add(_escape_for_rich(str(item)))
                else:
                    meta_branch.add(f"{meta_key}: []")
            else:
                meta_branch.add(f"{meta_key}: {_escape_for_rich(meta_value)}")

    console.print(tree)


def display_task_yaml(task_dict):
    """Display task as strict YAML via ruamel.yaml.

    Guaranteed conformant YAML output for piping to yq/jq.
    No hyperlinks, no Rich formatting.

    Parameters
    ----------
    task_dict : dict
        Task data dictionary with Link, Task, Boards, History, Metadata, etc.
    """
    yaml = YAML()
    yaml.default_flow_style = False

    # Build clean dict - use _url for the Link (plain URL string)
    output = {"Link": task_dict.get("_url", "")}

    # Add Task section
    if task_dict.get("Task"):
        output["Task"] = {k: v for k, v in task_dict["Task"].items()}

    # Add Assignee if present (extract plain text from Rich Text if needed)
    assignee = task_dict.get("_assignee")
    if assignee is not None:
        # Convert Rich Text to plain string, or use string directly
        if isinstance(assignee, Text):
            output["Assignee"] = assignee.plain
        else:
            output["Assignee"] = str(assignee)

    # Add Space if present (extract plain text from Rich Text if needed)
    space = task_dict.get("_space")
    if space is not None:
        # Convert Rich Text to plain string, or use string directly
        if isinstance(space, Text):
            output["Space"] = space.plain
        else:
            output["Space"] = str(space)

    # Add Boards section without internal keys
    if task_dict.get("Boards"):
        boards = {}
        for board_name, board_data in task_dict["Boards"].items():
            if isinstance(board_data, dict):
                boards[board_name] = {
                    k: v for k, v in board_data.items() if not k.startswith("_")
                }
            else:
                boards[board_name] = board_data
        output["Boards"] = boards

    # Always include Parents and Subtasks (even if empty list)
    output["Parents"] = task_dict.get("Parents", [])
    output["Subtasks"] = task_dict.get("Subtasks", [])

    # Add History section if present
    if task_dict.get("History"):
        output["History"] = task_dict["History"]

    # Add Comments section if present
    if task_dict.get("Comments"):
        # Convert PreservedScalarString to plain strings for strict YAML
        comments = []
        for comment in task_dict["Comments"]:
            if isinstance(comment, PreservedScalarString):
                comments.append(str(comment))
            else:
                comments.append(comment)
        output["Comments"] = comments

    # Add Metadata section if present
    if task_dict.get("Metadata"):
        output["Metadata"] = task_dict["Metadata"]

    stream = StringIO()
    yaml.dump([output], stream)
    print(stream.getvalue(), end="")


def display_task_json(task_dict):
    """Display task as JSON.

    Machine-readable JSON output for piping to jq or other tools.
    No hyperlinks, no Rich formatting.

    Parameters
    ----------
    task_dict : dict
        Task data dictionary with Link, Task, Boards, History, Metadata, etc.
    """
    # Build clean dict - use _url for the Link (plain URL string)
    output = {"Link": task_dict.get("_url", "")}

    # Add Task section
    if task_dict.get("Task"):
        output["Task"] = {}
        for k, v in task_dict["Task"].items():
            # Convert PreservedScalarString to plain string
            if isinstance(v, PreservedScalarString):
                output["Task"][k] = str(v)
            else:
                output["Task"][k] = v

    # Add Assignee if present (extract plain text from Rich Text if needed)
    assignee = task_dict.get("_assignee")
    if assignee is not None:
        # Convert Rich Text to plain string, or use string directly
        if isinstance(assignee, Text):
            output["Assignee"] = assignee.plain
        else:
            output["Assignee"] = str(assignee)

    # Add Space if present (extract plain text from Rich Text if needed)
    space = task_dict.get("_space")
    if space is not None:
        # Convert Rich Text to plain string, or use string directly
        if isinstance(space, Text):
            output["Space"] = space.plain
        else:
            output["Space"] = str(space)

    # Add Boards section without internal keys
    if task_dict.get("Boards"):
        boards = {}
        for board_name, board_data in task_dict["Boards"].items():
            if isinstance(board_data, dict):
                boards[board_name] = {
                    k: v for k, v in board_data.items() if not k.startswith("_")
                }
            else:
                boards[board_name] = board_data
        output["Boards"] = boards

    # Always include Parents and Subtasks (even if empty list)
    output["Parents"] = task_dict.get("Parents", [])
    output["Subtasks"] = task_dict.get("Subtasks", [])

    # Add History section if present
    if task_dict.get("History"):
        output["History"] = task_dict["History"]

    # Add Comments section if present
    if task_dict.get("Comments"):
        # Convert PreservedScalarString to plain strings
        comments = []
        for comment in task_dict["Comments"]:
            if isinstance(comment, PreservedScalarString):
                comments.append(str(comment))
            else:
                comments.append(comment)
        output["Comments"] = comments

    # Add Metadata section if present
    if task_dict.get("Metadata"):
        output["Metadata"] = task_dict["Metadata"]

    print(json.dumps(output, indent=2))


def display_tasks(result, output_format, phabfive_instance):
    """Display task search/show results in the specified format.

    Parameters
    ----------
    result : dict
        Result from task_search() or task_show() containing 'tasks' list
    output_format : str
        One of 'rich', 'tree', 'yaml', or 'json'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    """
    if not result or not result.get("tasks"):
        return

    console = phabfive_instance.get_console()

    try:
        for task_dict in result["tasks"]:
            if output_format == "tree":
                display_task_tree(console, task_dict, phabfive_instance)
            elif output_format in ("yaml", "strict"):
                display_task_yaml(task_dict)
            elif output_format == "json":
                display_task_json(task_dict)
            else:  # "rich" (default)
                display_task_rich(console, task_dict, phabfive_instance)
    except BrokenPipeError:
        # Handle pipe closed by consumer (e.g., head, less)
        # Quietly exit - this is normal behavior
        sys.stderr.close()
        sys.exit(0)
