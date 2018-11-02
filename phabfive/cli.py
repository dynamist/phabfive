# -*- coding: utf-8 -*-

# python std lib
import re
import sys

# phabfive imports
from phabfive import passphrase, diffusion, paste
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
    phabfive diffusion repo list [(active || inactive)] [options]
    phabfive diffusion repo create <name> [options]
    phabfive diffusion branch list <repo> [options]

Arguments:
    <repo>              Repository monogram (R123) or shortname, but currently
                        not the callsign

Options:
    -h, --help          Show this help message and exit
    -u, --url           Show url
"""

sub_paste_args = """
Usage:
    phabfive paste list 
    phabfive paste show <ids> ... [options]

Arguments:
    <ids> ...            Paste monogram (P123), example P1 P2 P3

Options:
    -h, --help           Show this help message and exit
"""


def parse_cli():
    """
    Split the functionality into two methos.

    One for parsing the cli and one that runs the application.
    """
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
        cli_args["<args>"] = [monogram]
        cli_args["<command>"] = app
        sub_args = docopt(eval("sub_{app}_args".format(app=app)), argv=argv)
    elif cli_args["<command>"] == "passphrase":
        sub_args = docopt(sub_passphrase_args, argv=argv)
    elif cli_args["<command>"] == "diffusion":
        sub_args = docopt(sub_diffusion_args, argv=argv)
    elif cli_args["<command>"] == "paste":
        sub_args = docopt(sub_paste_args, argv=argv)
    else:
        extras(True, phabfive.__version__, [Option("-h", "--help", 0, True)], base_args)
        sys.exit(1)

    sub_args["<sub_command>"] = cli_args["<args>"][0]

    return (cli_args, sub_args)


def run(cli_args, sub_args):
    """
    """
    retcode = 0

    try:
        if cli_args["<command>"] == "passphrase":
            p = passphrase.Passphrase()
            p.print_secret(sub_args["<id>"])
        elif cli_args["<command>"] == "diffusion":
            d = diffusion.Diffusion()
            if sub_args["repo"]:
                if sub_args["list"]:
                    if sub_args["active"]:
                        status = ["active"]
                    elif sub_args["inactive"]:
                        status = ["inactive"]
                    else:
                        status = REPO_STATUS_CHOICES
                    d.print_repositories(status=status, url=sub_args["--url"])
                elif sub_args["create"]:
                    d.print_created_repository_url(name=sub_args["<name>"])
            elif sub_args["branch"] and sub_args["list"]:
                d.print_branches(repo=sub_args["<repo>"])
        elif cli_args["<command>"] == "paste":
            p = paste.Paste()
            if sub_args["list"]:
                p.print_pastes()
            elif sub_args["show"]:
                if sub_args["<ids>"]:
                    p.print_pastes(ids=sub_args["<ids>"])

    except (
        PhabfiveConfigException,
        PhabfiveDataException,
        PhabfiveRemoteException,
    ) as e:
        print(e)
        retcode = 1

    return retcode


def cli_entrypoint():
    """
    Used by setup.py to create a cli entrypoint script
    """
    cli_args, sub_args = parse_cli()

    try:
        sys.exit(run(cli_args, sub_args))
    except Exception:
        raise
