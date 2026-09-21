# -*- coding: utf-8 -*-
"""How the command builds an app.

The classes are a library first, so their defaults suit a program: nothing is
requested until a call needs it. The command wants the opposite - to find out
about a bad token or an unreachable host before it prints anything - and it
wants a missing configuration answered by the setup wizard rather than a
traceback. Every command used to spell that out for itself, in seven copies;
this is the one place that says it now.
"""

import sys

import typer

from phabfive.exceptions import PhabfiveConfigException


def new_app(cls):
    """Construct `cls` the way the command always has.

    The configuration is discovered and the connection is verified, so a
    command fails before it has done anything rather than halfway through.
    """
    return cls(verify=True)


def get_app(cls):
    """Construct `cls`, answering a configuration or connection problem.

    A missing or malformed configuration offers the setup wizard and tries
    once more when it succeeds. A host that cannot be reached is one line on
    stderr. Both end the command with exit status 1 otherwise.
    """
    import requests

    try:
        return new_app(cls)
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return new_app(cls)
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


__all__ = ["get_app", "new_app"]
