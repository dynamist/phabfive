# -*- coding: utf-8 -*-

from __future__ import print_function

# python std lib
import re
import sys

# phabfive imports
from phabfive import passphrase, diffusion, paste, user
from phabfive.constants import MONOGRAMS, REPO_STATUS_CHOICES
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveRemoteException,
)

# 3rd party imports
from docopt import docopt


base_args = """
Usage:
    phabfive [-v ...] [options] <command> [<args> ...]

Available phabfive commands are:
    passphrase          The passphrase app
    diffusion           The diffusion app
    paste               The paste app
    user                Information on users

Shortcuts to Phabricator monograms:

    K[0-9]+             Passphrase object, example K123
    R[0-9]+             Diffusion repo, example R123
    P[0-9]+             Paste object, example P123

Options:
    -h, --help          Show this help message and exit
    -V, --version       Display the version number and exit
"""

sub_passphrase_args = """
Usage:
    phabfive passphrase <id> [options]

Options:
    -h, --help          Show this help message and exit

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
    <repo>              Repository monogram (R123) or shortname, but currently
                        not the callsign
    <uri>               ex. git@bitbucket.org:dynamist/webpage.git
    <credential>        SSH Private Key for read-only observing, stored in Passphrase ex. K123

Options:
    -h, --help                     Show this help message and exit

Repo List Options:
    -u, --url                      Show url

Uri List Options:
    -c, --clone                    Show clone url(s)

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
    <ids> ...            Paste monogram (P123), example P1 P2 P3
    <title>              Title for Paste
    <file>               A file with text content for Paste ex. myfile.txt

Options:
    -h, --help           Show this help message and exit

Paste Create Options:
    -t, --tags=<tags> ...           Project name(s), ex. --tags=projectX,projectY,projectZ
    -s, --subscribers=<sub> ...     Subscribers - user, project, mailing list name. Ex --subscribers=user1,user2,user3
"""

sub_user_args = """
Usage:
    phabfive user whoami [options]

Options:
    -h, --help           Show this help message and exit
"""


def parse_cli():
    """Parse the CLI arguments and options."""
    import phabfive

    from docopt import extras, Option, DocoptExit

    try:
        cli_args = docopt(
            base_args, options_first=True, version=phabfive.__version__, help=True
        )
    except DocoptExit:
        extras(True, phabfive.__version__, [Option("-h", "--help", 0, True)], base_args)

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
    else:
        extras(True, phabfive.__version__, [Option("-h", "--help", 0, True)], base_args)
        sys.exit(1)

    sub_args["<sub_command>"] = cli_args["<args>"][0]

    return (cli_args, sub_args)


def run(cli_args, sub_args):
    """Execute the CLI."""
    retcode = 0

    try:
        if cli_args["<command>"] == "passphrase":
            p = passphrase.Passphrase()
            p.print_secret(sub_args["<id>"])
        elif cli_args["<command>"] == "diffusion":
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
                    d.print_uri(repo=sub_args["<repo>"], clone_uri=sub_args["--clone"])
                elif sub_args["edit"]:
                    object_id = d.get_object_identifier(
                        repo_name=sub_args["<repo>"], uri_name=sub_args["<uri>"]
                    )
                    if sub_args["--enable"]:
                        disable = False
                    elif sub_args["--disable"]:
                        disable = True
                    else:
                        disable = None
                    if all(
                        a is None
                        for a in [
                            sub_args["--new_uri"],
                            sub_args["--io"],
                            sub_args["--display"],
                            sub_args["--cred"],
                            disable,
                        ]
                    ):
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
        elif cli_args["<command>"] == "paste":
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
