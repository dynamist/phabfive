# -*- coding: utf-8 -*-

# python std lib
import logging
import re
import sys
from io import StringIO

# 3rd party imports
from docopt import DocoptExit, Option, docopt, extras
from rich.text import Text
from rich.tree import Tree
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import PreservedScalarString

# phabfive imports
from phabfive.constants import MONOGRAMS

log = logging.getLogger(__name__)


# =============================================================================
# Maniphest Display Functions
# =============================================================================


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
    task_data = task_dict.get("Task", {})
    boards = task_dict.get("Boards", {})
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
                console.print(f"      {line}")
        elif _needs_yaml_quoting(value):
            escaped = value.replace("'", "''")
            console.print(f"    {key}: '{escaped}'")
        else:
            console.print(f"    {key}: {value}")

    # Print Assignee
    if assignee:
        console.print(Text.assemble("    Assignee: ", assignee))

    # Print Boards with clickable names
    if boards:
        console.print("  Boards:")
        for board_name, board_data in boards.items():
            project_slug = board_name.lower().replace(" ", "-")
            board_url = f"{phabfive_instance.url}/tag/{project_slug}/"
            board_link = phabfive_instance.format_link(board_url, board_name, show_url=False)
            console.print(Text.assemble("    ", board_link, ":"))

            if isinstance(board_data, dict):
                for key, value in board_data.items():
                    if key.startswith("_"):
                        continue
                    if key == "Column":
                        column_phid = board_data.get("_column_phid", "")
                        needs_quoting = _needs_yaml_quoting(value)
                        if column_phid:
                            query_url = (
                                f"{phabfive_instance.url}/maniphest/?columns={column_phid}"
                            )
                            column_link = phabfive_instance.format_link(
                                query_url, value, show_url=False
                            )
                            if needs_quoting:
                                # When hyperlinks enabled, column_link is Text; when disabled, it's str
                                if isinstance(column_link, Text):
                                    console.print(
                                        Text.assemble(
                                            "      Column: '", column_link, "'"
                                        )
                                    )
                                else:
                                    escaped = column_link.replace("'", "''")
                                    console.print(f"      Column: '{escaped}'")
                            else:
                                console.print(
                                    Text.assemble("      Column: ", column_link)
                                )
                            continue
                    if _needs_yaml_quoting(value):
                        escaped = value.replace("'", "''")
                        console.print(f"      {key}: '{escaped}'")
                    else:
                        console.print(f"      {key}: {value}")

    # Print History section
    if history:
        console.print("  History:")
        for hist_key, hist_value in history.items():
            if hist_key == "Boards" and isinstance(hist_value, dict):
                console.print("    Boards:")
                for board_name, transitions in hist_value.items():
                    console.print(f"      {board_name}:")
                    for trans in transitions:
                        console.print(f"        - {trans}")
            elif isinstance(hist_value, list):
                console.print(f"    {hist_key}:")
                for trans in hist_value:
                    console.print(f"      - {trans}")

    # Print Comments section
    comments = task_dict.get("Comments", [])
    if comments:
        console.print("  Comments:")
        for comment in comments:
            if isinstance(comment, PreservedScalarString) or "\n" in str(comment):
                # Multi-line comment
                lines = str(comment).splitlines()
                console.print(f"    - {lines[0]}")
                for line in lines[1:]:
                    console.print(f"      {line}")
            else:
                console.print(f"    - {comment}")

    # Print Metadata section
    if metadata:
        console.print("  Metadata:")
        for meta_key, meta_value in metadata.items():
            if isinstance(meta_value, list):
                if meta_value:
                    console.print(f"    {meta_key}:")
                    for item in meta_value:
                        console.print(f"      - {item}")
                else:
                    console.print(f"    {meta_key}: []")
            else:
                console.print(f"    {meta_key}: {meta_value}")


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
    task_data = task_dict.get("Task", {})
    boards = task_dict.get("Boards", {})
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
            task_branch.add(f"{key}: {first_line}")
        else:
            task_branch.add(f"{key}: {value}")

    # Add Assignee
    if assignee:
        task_branch.add(Text.assemble("Assignee: ", assignee))

    # Add Boards section
    if boards:
        boards_branch = tree.add("Boards")
        for board_name, board_data in boards.items():
            project_slug = board_name.lower().replace(" ", "-")
            board_url = f"{phabfive_instance.url}/tag/{project_slug}/"
            board_link = phabfive_instance.format_link(board_url, board_name, show_url=False)
            board_branch = boards_branch.add(board_link)

            if isinstance(board_data, dict):
                for key, value in board_data.items():
                    if key.startswith("_"):
                        continue
                    if key == "Column":
                        column_phid = board_data.get("_column_phid", "")
                        if column_phid:
                            query_url = (
                                f"{phabfive_instance.url}/maniphest/?columns={column_phid}"
                            )
                            column_link = phabfive_instance.format_link(
                                query_url, value, show_url=False
                            )
                            board_branch.add(Text.assemble("Column: ", column_link))
                            continue
                    board_branch.add(f"{key}: {value}")

    # Add History section
    if history:
        history_branch = tree.add("History")
        for hist_key, hist_value in history.items():
            if hist_key == "Boards" and isinstance(hist_value, dict):
                boards_hist = history_branch.add("Boards")
                for board_name, transitions in hist_value.items():
                    board_hist = boards_hist.add(board_name)
                    for trans in transitions:
                        board_hist.add(trans)
            elif isinstance(hist_value, list):
                hist_type_branch = history_branch.add(hist_key)
                for trans in hist_value:
                    hist_type_branch.add(trans)

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
                comments_branch.add(first_line)
            else:
                comments_branch.add(str(comment))

    # Add Metadata section
    if metadata:
        meta_branch = tree.add("Metadata")
        for meta_key, meta_value in metadata.items():
            if isinstance(meta_value, list):
                if meta_value:
                    list_branch = meta_branch.add(meta_key)
                    for item in meta_value:
                        list_branch.add(str(item))
                else:
                    meta_branch.add(f"{meta_key}: []")
            else:
                meta_branch.add(f"{meta_key}: {meta_value}")

    console.print(tree)


def display_task_strict(task_dict):
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


def display_tasks(result, output_format, phabfive_instance):
    """Display task search/show results in the specified format.

    Parameters
    ----------
    result : dict
        Result from task_search() or task_show() containing 'tasks' list
    output_format : str
        One of 'rich', 'tree', or 'strict'
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
            elif output_format == "strict":
                display_task_strict(task_dict)
            else:  # "rich" (default)
                display_task_rich(console, task_dict, phabfive_instance)
    except BrokenPipeError:
        # Handle pipe closed by consumer (e.g., head, less)
        # Quietly exit - this is normal behavior
        sys.stderr.close()
        sys.exit(0)

base_args = """
Usage:
    phabfive [options] <command> [<args> ...]

Available phabfive commands are:
    passphrase   The passphrase app
    diffusion    The diffusion app
    maniphest    The maniphest app
    paste        The paste app
    repl         Enter a REPL with API access
    user         Information on users

Shortcuts to view Phabricator monograms (example: phabfive T123):

    K[0-9]+   Passphrase secret
    P[0-9]+   Paste text
    R[0-9]+   Diffusion repo
    T[0-9]+   Maniphest task

Options:
    --log-level=LEVEL     Set loglevel [default: INFO]
    --format=FORMAT       Output format: rich (default), tree, or strict [default: rich]
    --ascii=WHEN          Use ASCII instead of Unicode (always/auto/never) [default: auto]
    --hyperlink=WHEN      Enable terminal hyperlinks (always/auto/never) [default: auto]
    -h, --help            Show this help message and exit
    -V, --version         Display the version number and exit
"""

sub_passphrase_args = """
Usage:
    phabfive passphrase <id> [options]

Options:
    -h, --help   Show this help message and exit

"""  # nosec-B105

sub_diffusion_args = """
Usage:
    phabfive diffusion repo list [(active || inactive || all)] [options]
    phabfive diffusion repo create <name> [options]
    phabfive diffusion uri list <repo> [options]
    phabfive diffusion uri create (--observe || --mirror) (<credential>) <repo> <uri> [options]
    phabfive diffusion uri edit <repo> <uri> [(--enable || --disable)] [options]
    phabfive diffusion branch list <repo> [options]

Arguments:
    <repo>         Repository monogram (R123) or shortname, but currently not the callsign
    <uri>          ex. git@bitbucket.org:dynamist/webpage.git
    <credential>   SSH Private Key for read-only observing, stored in Passphrase ex. K123

Options:
    -h, --help   Show this help message and exit

Repo List Options:
    -u, --url   Show url

Uri List Options:
    -c, --clone   Show clone url(s)

Uri Edit Options:
    -n, --new_uri=URI      Change repository URI
    -i, --io=VALUE         Adjust I/O behavior. Value: default, read, write, never
    -d, --display=VALUE    Change display behavior. Value: default, always, hidden
    -c, --cred=CREDENTIAL  Change credential for this URI. Ex. K2
"""

sub_paste_args = """
Usage:
    phabfive paste list
    phabfive paste create <title> <file> [options]
    phabfive paste show <ids> ... [options]

Arguments:
    <ids> ...   Paste monogram (P123), example P1 P2 P3
    <title>     Title for Paste
    <file>      A file with text content for Paste ex. myfile.txt

Options:
    -h, --help  Show this help message and exit

Paste Create Options:
    -t, --tags=TAGS ...           Project name(s), ex. --tags=projectX,projectY,projectZ
    -s, --subscribers=USERS ...   Subscribers - user, project, mailing list name. Ex --subscribers=user1,user2,user3
"""

sub_user_args = """
Usage:
    phabfive user whoami [options]

Options:
    -h, --help   Show this help message and exit
"""

sub_repl_args = """
Usage:
    phabfive repl [options]

Options:
    -h, --help  Show this help message and exit
"""

sub_maniphest_base_args = """
Usage:
    phabfive maniphest comment <ticket_id> <comment> [options]
    phabfive maniphest show <ticket_id> [options]
    phabfive maniphest create <title> [options]
    phabfive maniphest create --with=TEMPLATE [options]
    phabfive maniphest search <project_name> [options]

Options:
    -h, --help           Show this help message and exit
"""

sub_maniphest_show_args = """
Usage:
    phabfive maniphest show <ticket_id> [options]

Arguments:
    <ticket_id>          Task ID (e.g., T123)

Options:
    -H, --show-history   Display transition history for columns, priority, and status
    -M, --show-metadata  Display metadata about the task
    -C, --show-comments  Display comments on the task
    -h, --help           Show this help message and exit
"""

sub_maniphest_create_args = """
Usage:
    phabfive maniphest create <title> [--tag=TAG]... [--subscribe=USER]... [options]
    phabfive maniphest create --with=TEMPLATE [options]

Arguments:
    <title>              Task title (for CLI mode)

Options:
    --with=TEMPLATE         Load task creation template from YAML file (bulk mode)
    --description=TEXT      Task description (optional)
    --tag=TAG               Project/workboard tag (repeatable, or use + for multiple)
    --assign=USER           Assignee username
    --status=STATUS         Task status (Open, Resolved, Wontfix, Invalid, Duplicate, Spite)
    --priority=LEVEL        Task priority (Unbreak, Triage, High, Normal, Low, Wish)
    --subscribe=USER        Subscriber username (repeatable, or use + for multiple)
    --dry-run               Preview without creating task
    -h, --help              Show this help message and exit

Examples:
    # CLI mode - single task creation
    phabfive maniphest create 'Fix login bug' --tag DevTeam --assign hholm --priority High
    phabfive maniphest create 'New feature' --tag ProjectA --tag ProjectB --subscribe user1

    # Template mode - bulk creation
    phabfive maniphest create --with templates/task-create/project-setup.yaml
    phabfive maniphest create --with templates/task-create/sprint-planning.yaml --dry-run
"""

sub_maniphest_comment_args = """
Usage:
    phabfive maniphest comment <ticket_id> <comment> [options]

Arguments:
    <ticket_id>          Task ID (e.g., T123)
    <comment>            Comment text to add

Options:
    -h, --help           Show this help message and exit
"""

sub_maniphest_search_args = """
Usage:
    phabfive maniphest search [<text_query>] [options]

Arguments:
     <text_query>         Optional free-text search in task title/description.
                         If omitted, you must provide at least one filter option.

Options:
    --with=TEMPLATE      Load search parameters from a YAML template file.
                          Command-line options will override YAML values.
    --tag=PATTERN          Filter by project/workboard tag (supports OR/AND logic and wildcards).
                          Supports: "*" (all projects), "prefix*" (starts with),
                          "*suffix" (ends with), "*contains*" (contains text).
                          Filter syntax: "ProjectA,ProjectB" (OR), "ProjectA+ProjectB" (AND).
    --created-after=N      Tasks created within the last N days
    --updated-after=N      Tasks updated within the last N days
    --column=PATTERNS      Filter tasks by column transitions (comma=OR, plus=AND).
                           Automatically displays transition history.
                             from:COLUMN[:direction]  - Moved from COLUMN
                             to:COLUMN                - Moved to COLUMN
                             in:COLUMN                - Currently in COLUMN
                             been:COLUMN              - Was in COLUMN at any point
                             never:COLUMN             - Never was in COLUMN
                             backward                 - Any backward movement
                             forward                  - Any forward movement
                             not:PATTERN              - Negates any pattern above
                           Examples:
                             from:In Progress:forward
                             to:Done,in:Blocked
                             not:in:Done+been:Review
                             from:Up Next:forward+in:Done
    --priority=PATTERNS    Filter tasks by priority transitions (comma=OR, plus=AND).
                           Automatically displays priority history.
                             from:PRIORITY[:direction]  - Changed from PRIORITY
                             to:PRIORITY                - Changed to PRIORITY
                             in:PRIORITY                - Currently at PRIORITY
                             been:PRIORITY              - Was at PRIORITY at any point
                             never:PRIORITY             - Never was at PRIORITY
                             raised                     - Any priority increase
                             lowered                    - Any priority decrease
                             not:PATTERN                - Negates any pattern above
                           Examples:
                             been:Unbreak Now!
                             from:Normal:raised
                             not:in:High+raised
                             in:High,been:Unbreak Now!
    --status=PATTERNS      Filter tasks by status transitions (comma=OR, plus=AND).
                           Automatically displays status history.
                             from:STATUS[:direction]  - Changed from STATUS
                             to:STATUS                - Changed to STATUS
                             in:STATUS                - Currently at STATUS
                             been:STATUS              - Was at STATUS at any point
                             never:STATUS             - Never was at STATUS
                             raised                   - Status progressed forward
                             lowered                  - Status moved backward
                             not:PATTERN              - Negates any pattern above
                            Examples:
                              been:Open
                              from:Open:raised
                              not:in:Resolved+raised
                              in:Open,been:Resolved
     --show-history         Display column, priority, and status transition history
     --show-metadata        Display filter match metadata (which boards/priority/status matched)
     -h, --help             Show this help message and exit

Examples:
    # Free-text search
    phabfive maniphest search 'Lets Encrypt'
    phabfive maniphest search 'Lets Encrypt' --status 'in:Resolved'

    # Tag search (project/workboard filtering)
    phabfive maniphest search --tag Developer-Experience
    phabfive maniphest search --tag Developer-Experience --updated-after 7

    # Combined search
    phabfive maniphest search OpenStack --tag System-Board --updated-after 7

    # Using YAML templates
    phabfive maniphest search --with templates/task-search/tasks-resolved-but-not-in-done.yaml
    phabfive maniphest search --with templates/task-search/search-template.yaml --tag Override-Project
"""


def parse_cli():
    """
    Parse the CLI arguments and options
    """
    import phabfive

    try:
        cli_args = docopt(
            base_args,
            options_first=True,
            version=phabfive.__version__,
            default_help=True,
        )
    except DocoptExit:
        extras(
            True,
            phabfive.__version__,
            [Option("-h", "--help", 0, True)],
            base_args,
        )

    phabfive.init_logging(cli_args["--log-level"])

    argv = [cli_args["<command>"]] + cli_args["<args>"]

    patterns = re.compile("^(?:" + "|".join(MONOGRAMS.values()) + ")")

    # First check for monogram shortcuts, i.e. invocation with `phabfive K123`
    # instead of the full `phabfive passphrase K123`
    if patterns.search(cli_args["<command>"]):
        monogram = cli_args["<command>"]
        app = {MONOGRAMS[k][0]: k for k in MONOGRAMS.keys()}[monogram[0]]

        # Patch the arguments to fool docopt into thinking we are the app
        if app == "passphrase":
            argv = [app] + argv
        elif app == "diffusion":
            argv = [app, "branch", "list"] + argv
        elif app == "paste":
            argv = [app, "show"] + argv
        elif app == "user":
            argv = [app, "whoami"] + argv
        elif app == "maniphest":
            # Check if there's a non-option argument (comment text)
            # Options start with '-', positional args are comment text
            has_comment_text = len(argv) > 1 and not argv[1].startswith("-")
            if has_comment_text:
                # T123 'my comment' -> maniphest comment T123 'my comment'
                argv = [app, "comment"] + argv
            else:
                # T123 -> maniphest show T123
                # T123 -C -> maniphest show T123 -C
                argv = [app, "show"] + argv

        cli_args["<args>"] = [monogram]
        cli_args["<command>"] = app

        # For maniphest shortcuts, use the appropriate docopt schema
        if app == "maniphest":
            if "comment" in argv:
                sub_args = docopt(sub_maniphest_comment_args, argv=argv)
            else:
                sub_args = docopt(sub_maniphest_show_args, argv=argv)
        else:
            sub_args = docopt(eval("sub_{app}_args".format(app=app)), argv=argv)  # nosec-B307
    elif cli_args["<command>"] == "passphrase":
        sub_args = docopt(sub_passphrase_args, argv=argv)
    elif cli_args["<command>"] == "diffusion":
        sub_args = docopt(sub_diffusion_args, argv=argv)
    elif cli_args["<command>"] == "paste":
        sub_args = docopt(sub_paste_args, argv=argv)
    elif cli_args["<command>"] == "user":
        sub_args = docopt(sub_user_args, argv=argv)
    elif cli_args["<command>"] == "repl":
        sub_args = docopt(sub_repl_args, argv=argv)
    elif cli_args["<command>"] == "maniphest":
        # Determine which maniphest subcommand is being called
        maniphest_subcmd = None
        if len(argv) > 1:
            if argv[1] == "show":
                maniphest_subcmd = "show"
            elif argv[1] == "create":
                maniphest_subcmd = "create"
            elif argv[1] == "search":
                maniphest_subcmd = "search"
            elif argv[1] == "comment":
                maniphest_subcmd = "comment"

        # Use the appropriate help string based on subcommand
        if maniphest_subcmd == "show":
            sub_args = docopt(sub_maniphest_show_args, argv=argv)
        elif maniphest_subcmd == "create":
            sub_args = docopt(sub_maniphest_create_args, argv=argv)
        elif maniphest_subcmd == "search":
            sub_args = docopt(sub_maniphest_search_args, argv=argv)
        elif maniphest_subcmd == "comment":
            sub_args = docopt(sub_maniphest_comment_args, argv=argv)
        else:
            # No subcommand or unrecognized subcommand - show base help
            sub_args = docopt(sub_maniphest_base_args, argv=argv)
    else:
        extras(
            True,
            phabfive.__version__,
            [Option("-h", "--help", 0, True)],
            base_args,
        )
        sys.exit(1)

    if len(cli_args["<args>"]) > 0:
        sub_args["<sub_command>"] = cli_args["<args>"][0]

    return (cli_args, sub_args)


def run(cli_args, sub_args):
    """
    Execute the CLI
    """
    # Local imports required due to logging limitation
    from phabfive import diffusion, maniphest, passphrase, paste, repl, user
    from phabfive.constants import REPO_STATUS_CHOICES
    from phabfive.core import Phabfive
    from phabfive.exceptions import PhabfiveException
    from phabfive.maniphest_transitions import parse_transition_patterns
    from phabfive.priority_transitions import parse_priority_patterns

    # Validate and process output options
    valid_modes = ("always", "auto", "never")
    valid_formats = ("rich", "tree", "strict")
    output_format = cli_args.get("--format", "rich")
    ascii_when = cli_args.get("--ascii", "never")
    hyperlink_when = cli_args.get("--hyperlink", "never")

    if output_format not in valid_formats:
        sys.exit(f"ERROR - --format must be one of: {', '.join(valid_formats)}")
    if ascii_when not in valid_modes:
        sys.exit(f"ERROR - --ascii must be one of: {', '.join(valid_modes)}")
    if hyperlink_when not in valid_modes:
        sys.exit(f"ERROR - --hyperlink must be one of: {', '.join(valid_modes)}")

    # Check mutual exclusivity
    if ascii_when == "always" and hyperlink_when == "always":
        sys.exit("ERROR - --ascii=always and --hyperlink=always are mutually exclusive")
    if output_format == "strict" and hyperlink_when == "always":
        sys.exit(
            "ERROR - --format=strict and --hyperlink=always are mutually exclusive"
        )

    # Set output formatting options
    Phabfive.set_output_options(
        ascii_when=ascii_when,
        hyperlink_when=hyperlink_when,
        output_format=output_format,
    )

    retcode = 0

    try:
        if cli_args["<command>"] == "passphrase":
            passphrase_app = passphrase.Passphrase()
            secret = passphrase_app.get_secret(sub_args["<id>"])
            print(secret)

        if cli_args["<command>"] == "diffusion":
            diffusion_app = diffusion.Diffusion()

            if sub_args["repo"]:
                if sub_args["list"]:
                    if sub_args["all"]:
                        status = REPO_STATUS_CHOICES
                    elif sub_args["inactive"]:
                        status = ["inactive"]
                    else:  # default value
                        status = ["active"]

                    repos = diffusion_app.get_repositories_formatted(
                        status=status, include_url=sub_args["--url"]
                    )
                    for repo in repos:
                        if sub_args["--url"]:
                            print(", ".join(repo["urls"]))
                        else:
                            print(repo["name"])
                elif sub_args["create"]:
                    diffusion_app.create_repository(name=sub_args["<name>"])
            elif sub_args["uri"]:
                if sub_args["create"]:
                    if sub_args["--mirror"]:
                        io = "mirror"
                        display = "always"
                    elif sub_args["--observe"]:
                        io = "observe"
                        display = "always"

                    created_uri = diffusion_app.create_uri(
                        repository_name=sub_args["<repo>"],
                        new_uri=sub_args["<uri>"],
                        io=io,
                        display=display,
                        credential=sub_args["<credential>"],
                    )
                    print(created_uri)
                elif sub_args["list"]:
                    uris = diffusion_app.get_uris_formatted(
                        repo=sub_args["<repo>"],
                        clone_uri=sub_args["--clone"],
                    )
                    for uri in uris:
                        print(uri)
                elif sub_args["edit"]:
                    object_id = diffusion_app.get_object_identifier(
                        repo_name=sub_args["<repo>"],
                        uri_name=sub_args["<uri>"],
                    )

                    if sub_args["--enable"]:
                        disable = False
                    elif sub_args["--disable"]:
                        disable = True
                    else:
                        disable = None

                    _data = [
                        sub_args["--new_uri"],
                        sub_args["--io"],
                        sub_args["--display"],
                        sub_args["--cred"],
                        disable,
                    ]

                    if all(arg is None for arg in _data):
                        print("Please input minimum one option")

                    result = diffusion_app.edit_uri(
                        uri=sub_args["--new_uri"],
                        io=sub_args["--io"],
                        display=sub_args["--display"],
                        credential=sub_args["--cred"],
                        disable=disable,
                        object_identifier=object_id,
                    )

                    if result:
                        print("OK")
            elif sub_args["branch"] and sub_args["list"]:
                branches = diffusion_app.get_branches_formatted(repo=sub_args["<repo>"])
                for branch_name in branches:
                    print(branch_name)

        if cli_args["<command>"] == "paste":
            paste_app = paste.Paste()

            if sub_args["list"]:
                pastes = paste_app.get_pastes_formatted()
                for p in pastes:
                    print(f"{p['id']} {p['title']}")
            elif sub_args["create"]:
                tags_list = None
                subscribers_list = None

                if sub_args["--tags"]:
                    tags_list = sub_args["--tags"].split(",")

                if sub_args["--subscribers"]:
                    subscribers_list = sub_args["--subscribers"].split(",")

                paste_app.create_paste(
                    title=sub_args["<title>"],
                    file=sub_args["<file>"],
                    tags=tags_list,
                    subscribers=subscribers_list,
                )
            elif sub_args["show"]:
                if sub_args["<ids>"]:
                    pastes = paste_app.get_pastes_formatted(ids=sub_args["<ids>"])
                    for p in pastes:
                        print(f"{p['id']} {p['title']}")

        if cli_args["<command>"] == "user":
            user_app = user.User()

            if sub_args["whoami"]:
                whoami_data = user_app.get_whoami()
                for key, value in whoami_data.items():
                    print(f"{key}: {value}")

        if cli_args["<command>"] == "repl":
            repl_app = repl.Repl()
            repl_app.run()

        if cli_args["<command>"] == "maniphest":
            maniphest_app = maniphest.Maniphest()

            if sub_args.get("search"):
                # Load YAML configurations if --with is provided
                search_configs = []
                if sub_args.get("--with"):
                    try:
                        search_configs = maniphest_app._load_search_from_yaml(
                            sub_args["--with"]
                        )
                    except Exception as e:
                        print(
                            f"ERROR: Failed to load template file: {e}",
                            file=sys.stderr,
                        )
                        retcode = 1
                        return retcode
                else:
                    # Create a single search config from CLI parameters
                    search_configs = [
                        {
                            "search": {},
                            "title": "Command Line Search",
                            "description": None,
                        }
                    ]

                # Helper function to get value with CLI override priority
                def get_param(cli_key, yaml_params, yaml_key=None, default=None):
                    if yaml_key is None:
                        yaml_key = cli_key.lstrip("-")

                    # CLI takes precedence over YAML
                    cli_value = sub_args.get(cli_key)
                    if cli_value is not None:
                        return cli_value

                    # Fall back to YAML, then default
                    return yaml_params.get(yaml_key, default)

                # Execute each search configuration
                for i, config in enumerate(search_configs):
                    yaml_params = config["search"]

                    # Print search header if multiple searches or if title/description provided
                    if (
                        len(search_configs) > 1
                        or config["title"] != "Command Line Search"
                    ):
                        print(f"\n{'=' * 60}")
                        print(f"🔍 {config['title']}")
                        if config["description"]:
                            print(f"📝 {config['description']}")
                        print(f"{'=' * 60}")

                    # Parse filter patterns with CLI override priority
                    transition_patterns = None
                    column_pattern = get_param("--column", yaml_params, "column")
                    if column_pattern:
                        try:
                            transition_patterns = parse_transition_patterns(
                                column_pattern
                            )
                        except Exception as e:
                            print(
                                f"ERROR: Invalid column filter pattern in {config['title']}: {e}",
                                file=sys.stderr,
                            )
                            retcode = 1
                            return retcode

                    priority_patterns = None
                    priority_pattern = get_param("--priority", yaml_params, "priority")
                    if priority_pattern:
                        try:
                            priority_patterns = parse_priority_patterns(
                                priority_pattern
                            )
                        except Exception as e:
                            print(
                                f"ERROR: Invalid priority filter pattern in {config['title']}: {e}",
                                file=sys.stderr,
                            )
                            retcode = 1
                            return retcode

                    status_patterns = None
                    status_pattern = get_param("--status", yaml_params, "status")
                    if status_pattern:
                        try:
                            # Parse status patterns with API-fetched status ordering
                            status_patterns = (
                                maniphest_app.parse_status_patterns_with_api(
                                    status_pattern
                                )
                            )
                        except Exception as e:
                            print(
                                f"ERROR: Invalid status filter pattern in {config['title']}: {e}",
                                file=sys.stderr,
                            )
                            retcode = 1
                            return retcode

                    # Get other parameters with CLI override priority
                    show_history = get_param(
                        "--show-history", yaml_params, "show-history", False
                    )
                    show_metadata = get_param(
                        "--show-metadata", yaml_params, "show-metadata", False
                    )
                    text_query = get_param("<text_query>", yaml_params, "text_query")
                    tag = get_param("--tag", yaml_params, "tag")
                    created_after = get_param(
                        "--created-after", yaml_params, "created-after"
                    )
                    updated_after = get_param(
                        "--updated-after", yaml_params, "updated-after"
                    )

                    # Check if any search criteria provided, show usage if not
                    has_criteria = any(
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
                    if not has_criteria:
                        print("Usage:")
                        print("    phabfive maniphest search [<text_query>] [options]")
                        return retcode

                    result = maniphest_app.task_search(
                        text_query=text_query,
                        tag=tag,
                        created_after=created_after,
                        updated_after=updated_after,
                        transition_patterns=transition_patterns,
                        priority_patterns=priority_patterns,
                        status_patterns=status_patterns,
                        show_history=show_history,
                        show_metadata=show_metadata,
                    )
                    display_tasks(result, output_format, maniphest_app)

            if sub_args.get("create"):
                # Check if template mode or CLI mode
                if sub_args.get("--with"):
                    result = maniphest_app.create_from_config(
                        sub_args["--with"],
                        dry_run=sub_args.get("--dry-run", False),
                    )
                    if result and result.get("dry_run"):
                        for task in result["tasks"]:
                            indent = "  " * task["depth"]
                            print(f"{indent}- {task['title']}")
                elif sub_args.get("<title>"):
                    result = maniphest_app.create_task_cli(
                        title=sub_args["<title>"],
                        description=sub_args.get("--description"),
                        tags=sub_args.get("--tag"),
                        assignee=sub_args.get("--assign"),
                        status=sub_args.get("--status"),
                        priority=sub_args.get("--priority"),
                        subscribers=sub_args.get("--subscribe"),
                        dry_run=sub_args.get("--dry-run", False),
                    )
                    if result:
                        if result.get("dry_run"):
                            # Display dry-run output
                            print("\n--- DRY RUN ---")
                            print(f"Title: {result['title']}")
                            if result.get("description"):
                                print(f"Description: {result['description']}")
                            if result.get("priority"):
                                print(f"Priority: {result['priority']}")
                            if result.get("status"):
                                print(f"Status: {result['status']}")
                            if result.get("assignee"):
                                print(f"Assignee: {result['assignee']}")
                            if result.get("tags"):
                                print(f"Tags: {', '.join(result['tags'])}")
                            if result.get("subscribers"):
                                print(f"Subscribers: {', '.join(result['subscribers'])}")
                            print("--- END DRY RUN ---\n")
                        else:
                            print(result["uri"])
                            # Print clickable tag URLs if any tags were added
                            if result.get("tag_slugs"):
                                for slug in result["tag_slugs"]:
                                    print(f"{result['base_url']}/tag/{slug}/")
                else:
                    print(
                        "ERROR: Must provide either a title or --with=TEMPLATE",
                        file=sys.stderr,
                    )
                    retcode = 1
                    return retcode

            if sub_args.get("comment"):
                result = maniphest_app.add_comment(
                    sub_args["<ticket_id>"],
                    sub_args["<comment>"],
                )

                if result[0]:
                    # Query the ticket to fetch the URI for it
                    _, ticket = maniphest_app.info(int(sub_args["<ticket_id>"][1:]))
                    print(ticket["uri"])

            if sub_args.get("show"):
                # Use new unified task_show() method
                ticket_id = sub_args["<ticket_id>"]

                # Validate ticket ID format using MONOGRAMS pattern
                maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
                if not re.match(maniphest_pattern, ticket_id):
                    log.critical(
                        f"Invalid task ID '{ticket_id}'. Expected format: T123"
                    )
                    return 1

                task_id = int(ticket_id[1:])

                # Handle flags
                show_history = sub_args.get("--show-history", False)
                show_metadata = sub_args.get("--show-metadata", False)
                show_comments = sub_args.get("--show-comments", False)

                result = maniphest_app.task_show(
                    task_id,
                    show_history=show_history,
                    show_metadata=show_metadata,
                    show_comments=show_comments,
                )
                display_tasks(result, output_format, maniphest_app)
    except PhabfiveException as e:
        # Catch all types of phabricator base exceptions
        log.critical(str(e))
        retcode = 1

    return retcode


def cli_entrypoint():
    """Used by setup.py to create a cli entrypoint script."""
    cli_args, sub_args = parse_cli()

    try:
        sys.exit(run(cli_args, sub_args))
    except Exception:
        raise
