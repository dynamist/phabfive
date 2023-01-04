# -*- coding: utf-8 -*-

# python std lib
import re
import sys
import logging
import logging.config
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

Shortcuts to Phabricator monograms:

    K[0-9]+   Passphrase object, example K123
    R[0-9]+   Diffusion repo, example R123
    P[0-9]+   Paste object, example P123

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

"""

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

Options:
    --all        Show all fields for a ticket
    --dry-run    Does everything except commiting the tickets
    --pp         Show all fields rendering with pretty print
    -h, --help   Show this help message and exit
"""


def parse_cli():
    """Parse the CLI arguments and options."""
    import phabfive

    try:
        cli_args = docopt(
            base_args,
            options_first=True,
            version=phabfive.__version__,
            help=True,
        )
    except DocoptExit:
        extras(
            True,
            phabfive.__version__,
            [Option("-h", "--help", 0, True)],
            base_args,
        )

    phabfive.init_logging(cli_args["--log-level"])
    log = logging.getLogger(__name__)

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
        sub_args = docopt(eval("sub_{app}_args".format(app=app)), argv=argv)
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
    """Execute the CLI"""
    # Local imports required due to logging limitation
    from phabfive import passphrase, diffusion, paste, user, repl, maniphest
    from phabfive.constants import REPO_STATUS_CHOICES 
    from phabfive.exceptions import (
        PhabfiveConfigException,
        PhabfiveDataException,
        PhabfiveRemoteException,
    )

    retcode = 0

    try:
        if cli_args["<command>"] == "passphrase":
            p = passphrase.Passphrase()
            p.print_secret(sub_args["<id>"])

        if cli_args["<command>"] == "diffusion":
            d = diffusion.Diffusion()

            if sub_args["repo"]:
                if sub_args["list"]:
                    if sub_args["all"]:
                        status = REPO_STATUS_CHOICES
                    elif sub_args["inactive"]:
                        status = ["inactive"]
                    else:  # default value
                        status = ["active"]

                    d.print_repositories(status=status, url=sub_args["--url"])
                elif sub_args["create"]:
                    d.create_repository(name=sub_args["<name>"])
            elif sub_args["uri"]:
                if sub_args["create"]:
                    if sub_args["--mirror"]:
                        io = "mirror"
                        display = "always"
                    elif sub_args["--observe"]:
                        io = "observe"
                        display = "always"

                    created_uri = d.create_uri(
                        repository_name=sub_args["<repo>"],
                        new_uri=sub_args["<uri>"],
                        io=io,
                        display=display,
                        credential=sub_args["<credential>"],
                    )
                    print(created_uri)
                elif sub_args["list"]:
                    d.print_uri(
                        repo=sub_args["<repo>"],
                        clone_uri=sub_args["--clone"],
                    )
                elif sub_args["edit"]:
                    object_id = d.get_object_identifier(
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

                    if all(a is None for a in _data):
                        print("Please input minimum one option")

                    result = d.edit_uri(
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
                d.print_branches(repo=sub_args["<repo>"])

        if cli_args["<command>"] == "paste":
            p = paste.Paste()

            if sub_args["list"]:
                p.print_pastes()
            elif sub_args["create"]:
                tags_list = None
                subscribers_list = None

                if sub_args["--tags"]:
                    tags_list = sub_args["--tags"].split(",")

                if sub_args["--subscribers"]:
                    subscribers_list = sub_args["--subscribers"].split(",")

                p.create_paste(
                    title=sub_args["<title>"],
                    file=sub_args["<file>"],
                    tags=tags_list,
                    subscribers=subscribers_list,
                )
            elif sub_args["show"]:
                if sub_args["<ids>"]:
                    p.print_pastes(ids=sub_args["<ids>"])

        if cli_args["<command>"] == "user":
            u = user.User()

            if sub_args["whoami"]:
                u.print_whoami()

        if cli_args["<command>"] == "repl":
            r = repl.Repl()
            r.run()

        if cli_args["<command>"] == "maniphest":
            m = maniphest.Maniphest()

            if sub_args["create"]:
                # This part is responsible for bulk creating several tickets at once
                m.create_from_config(
                    sub_args["<config-file>"],
                    dry_run = sub_args["--dry-run"],
                )

            if sub_args["comment"] and sub_args["add"]:
                result = m.add_comment(sub_args["<ticket_id>"], sub_args["<comment>"],)

                if result[0]:
                    # Query the ticket to fetch the URI for it
                    _, ticket = m.info(int(sub_args["<ticket_id>"][1:]))

                    print("Comment successfully added")
                    print("Ticket URI: {0}".format(ticket["uri"]))

            if sub_args["show"]:
                _, result = m.info(int(sub_args["<ticket_id>"][1:]))

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

                    date_created = datetime.fromtimestamp(int(result['dateCreated']))
                    print(f"dateCreated:    {date_created}")

                    date_modified = datetime.fromtimestamp(int(result['dateModified']))
                    print(f"dateModified:   {date_modified}")

                    print(f"dependsOnTaskPHIDs: {result['dependsOnTaskPHIDs']}")
                else:
                    print(f"Ticket ID:     {result['id']}")
                    print(f"phid:          {result['phid']}")
                    print(f"status:        {result['status']}")
                    print(f"priority:      {result['priority']}")
                    print(f"title:         {result['title']}")
                    print(f"uri:           {result['uri']}")
                    date_created = datetime.fromtimestamp(int(result['dateCreated']))
                    print(f"dateCreated:   {date_created}")

                    date_modified = datetime.fromtimestamp(int(result['dateModified']))
                    print(f"dateModified:  {date_modified}")
    except (
        PhabfiveConfigException,
        PhabfiveDataException,
        PhabfiveRemoteException,
    ) as e:
        print(str(e), file=sys.stderr)
        retcode = 1

    return retcode


def cli_entrypoint():
    """Used by setup.py to create a cli entrypoint script."""
    cli_args, sub_args = parse_cli()

    try:
        sys.exit(run(cli_args, sub_args))
    except Exception:
        raise
