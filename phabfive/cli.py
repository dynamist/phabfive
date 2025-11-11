# -*- coding: utf-8 -*-

# python std lib
import re
import sys
from datetime import datetime
from pprint import pprint as pp

# 3rd party imports
from docopt import docopt, extras, Option, DocoptExit


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
    --log-level=<level>   Set loglevel [default: INFO]
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
    -n, --new_uri=<value>  Change repository URI
    -i, --io=<value>       Adjust I/O behavior. Value: default, read, write, never
    -d, --display=<value>  Change display behavior. Value: default, always, hidden
    -c, --cred=<value>     Change credential for this URI. Ex. K2
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
    -t, --tags=<tags> ...         Project name(s), ex. --tags=projectX,projectY,projectZ
    -s, --subscribers=<sub> ...   Subscribers - user, project, mailing list name. Ex --subscribers=user1,user2,user3
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

sub_maniphest_args = """
Usage:
    phabfive maniphest comment add <ticket_id> <comment> [options]
    phabfive maniphest show <ticket_id> ([--all] | [--pp]) [options]
    phabfive maniphest create <config-file> [--dry-run] [options]
    phabfive maniphest search <project_name> [options]

Search Arguments:
    <project_name>       Project name or wildcard pattern.
                         Supports: "*" (all projects), "prefix*" (starts with),
                         "*suffix" (ends with), "*contains*" (contains text).
                         Empty string "" returns no results.

Search Options:
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

Options:
    --all                Show all fields for a ticket
    --dry-run            Does everything except commiting the tickets
    --pp                 Show all fields rendering with pretty print
    -h, --help           Show this help message and exit
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

    from phabfive.constants import MONOGRAMS

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
            argv = [app, "show"] + argv

        cli_args["<args>"] = [monogram]
        cli_args["<command>"] = app
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
        sub_args = docopt(sub_maniphest_args, argv=argv)
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
    from phabfive import passphrase, diffusion, paste, user, repl, maniphest
    from phabfive.maniphest_transitions import parse_transition_patterns
    from phabfive.priority_transitions import parse_priority_patterns
    from phabfive.status_transitions import parse_status_patterns
    from phabfive.constants import REPO_STATUS_CHOICES
    from phabfive.exceptions import PhabfiveException

    retcode = 0

    try:
        if cli_args["<command>"] == "passphrase":
            passphrase_app = passphrase.Passphrase()
            passphrase_app.print_secret(sub_args["<id>"])

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

                    diffusion_app.print_repositories(
                        status=status, url=sub_args["--url"]
                    )
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
                    diffusion_app.print_uri(
                        repo=sub_args["<repo>"],
                        clone_uri=sub_args["--clone"],
                    )
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
                diffusion_app.print_branches(repo=sub_args["<repo>"])

        if cli_args["<command>"] == "paste":
            paste_app = paste.Paste()

            if sub_args["list"]:
                paste_app.print_pastes()
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
                    paste_app.print_pastes(ids=sub_args["<ids>"])

        if cli_args["<command>"] == "user":
            user_app = user.User()

            if sub_args["whoami"]:
                user_app.print_whoami()

        if cli_args["<command>"] == "repl":
            repl_app = repl.Repl()
            repl_app.run()

        if cli_args["<command>"] == "maniphest":
            maniphest_app = maniphest.Maniphest()

            if sub_args["search"]:
                # Parse filter patterns if provided
                transition_patterns = None
                if sub_args.get("--column"):
                    try:
                        transition_patterns = parse_transition_patterns(
                            sub_args["--column"]
                        )
                    except Exception as e:
                        print(f"ERROR: Invalid column filter pattern: {e}", file=sys.stderr)
                        retcode = 1
                        return retcode

                priority_patterns = None
                if sub_args.get("--priority"):
                    try:
                        priority_patterns = parse_priority_patterns(
                            sub_args["--priority"]
                        )
                    except Exception as e:
                        print(f"ERROR: Invalid priority filter pattern: {e}", file=sys.stderr)
                        retcode = 1
                        return retcode

                status_patterns = None
                if sub_args.get("--status"):
                    try:
                        status_patterns = parse_status_patterns(
                            sub_args["--status"]
                        )
                    except Exception as e:
                        print(f"ERROR: Invalid status filter pattern: {e}", file=sys.stderr)
                        retcode = 1
                        return retcode

                # Only show history if explicitly requested
                show_history = sub_args.get("--show-history", False)

                show_metadata = sub_args.get("--show-metadata", False)

                maniphest_app.task_search(
                    sub_args["<project_name>"],
                    created_after=sub_args["--created-after"],
                    updated_after=sub_args["--updated-after"],
                    transition_patterns=transition_patterns,
                    priority_patterns=priority_patterns,
                    status_patterns=status_patterns,
                    show_history=show_history,
                    show_metadata=show_metadata,
                )

            if sub_args["create"]:
                # This part is responsible for bulk creating several tickets at once
                maniphest_app.create_from_config(
                    sub_args["<config-file>"],
                    dry_run=sub_args["--dry-run"],
                )

            if sub_args["comment"] and sub_args["add"]:
                result = maniphest_app.add_comment(
                    sub_args["<ticket_id>"],
                    sub_args["<comment>"],
                )

                if result[0]:
                    # Query the ticket to fetch the URI for it
                    _, ticket = maniphest_app.info(int(sub_args["<ticket_id>"][1:]))

                    print("Comment successfully added")
                    print("Ticket URI: {0}".format(ticket["uri"]))

            if sub_args["show"]:
                _, result = maniphest_app.info(int(sub_args["<ticket_id>"][1:]))

                if sub_args["--pp"]:
                    pp({key: value for key, value in result.items()})
                elif sub_args["--all"]:
                    print(f"Ticket ID:      {result['id']}")
                    print(f"phid:           {result['phid']}")
                    print(f"authorPHID:     {result['authorPHID']}")
                    print(f"ownerPHID:      {result['ownerPHID']}")
                    print(f"ccPHIDs:        {result['ccPHIDs']}")
                    print(f"status:         {result['status']}")
                    print(f"statusName:     {result['statusName']}")
                    print(f"isClosed:       {result['isClosed']}")
                    print(f"priority:       {result['priority']}")
                    print(f"priorityColor:  {result['priorityColor']}")
                    print(f"title:          {result['title']}")
                    print(f"description:    {result['description']}")
                    print(f"projectPHIDs:   {result['projectPHIDs']}")
                    print(f"uri:            {result['uri']}")
                    print(f"auxiliary:      {result['auxiliary']}")
                    print(f"objectName:     {result['objectName']}")

                    date_created = datetime.fromtimestamp(int(result["dateCreated"]))
                    print(f"dateCreated:    {date_created}")

                    date_modified = datetime.fromtimestamp(int(result["dateModified"]))
                    print(f"dateModified:   {date_modified}")

                    print(f"dependsOnTaskPHIDs: {result['dependsOnTaskPHIDs']}")

                    # Display workboard transition history
                    task_phid = result.get("phid")
                    if task_phid:
                        maniphest_app._display_task_transitions(task_phid)
                else:
                    print(f"Ticket ID:     {result['id']}")
                    print(f"phid:          {result['phid']}")
                    print(f"status:        {result['status']}")
                    print(f"priority:      {result['priority']}")
                    print(f"title:         {result['title']}")
                    print(f"uri:           {result['uri']}")
                    date_created = datetime.fromtimestamp(int(result["dateCreated"]))
                    print(f"dateCreated:   {date_created}")

                    date_modified = datetime.fromtimestamp(int(result["dateModified"]))
                    print(f"dateModified:  {date_modified}")
    except PhabfiveException as e:
        # Catch all types of phabricator base exceptions
        print(f"CRITICAL :: {str(e)}", file=sys.stderr)
        retcode = 1

    return retcode


def cli_entrypoint():
    """Used by setup.py to create a cli entrypoint script."""
    cli_args, sub_args = parse_cli()

    try:
        sys.exit(run(cli_args, sub_args))
    except Exception:
        raise
