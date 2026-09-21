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

from phabfive.exceptions import PhabfiveConfigException, PhabfiveConnectionException


def prompt_host(hosts):
    """Ask which of several ~/.arcrc hosts to use, when there is someone to ask.

    Returns None without a terminal, which leaves phabfive to explain how to
    choose one - a pipeline or a cron job must never block on a prompt.
    """
    if not sys.stdin.isatty():
        return None

    from InquirerPy import inquirer

    selected = inquirer.select(
        message="Multiple hosts found in ~/.arcrc. Select server:",
        choices=list(hosts),
    ).execute()
    print(
        f"Tip: to skip this prompt, set PHAB_URL or add "
        f'"config":{{"default":"{selected}"}} to ~/.arcrc',
        file=sys.stderr,
    )
    return selected


def new_app(cls):
    """Construct `cls` the way the command always has.

    The configuration is discovered and the connection is verified, so a
    command fails before it has done anything rather than halfway through.
    Several ~/.arcrc hosts and nothing to choose between them is answered
    with a prompt at a terminal.
    """
    app = cls(verify=True, select_host=prompt_host)
    # PHAB_FALLBACK is configuration, but the format it names is the
    # command's concern, so it is the command that applies it.
    app.set_fallback_format(app.conf.get("PHAB_FALLBACK", "yaml"))
    return app


def get_app(cls):
    """Construct `cls`, answering a configuration or connection problem.

    A missing or malformed configuration offers the setup wizard and tries
    once more when it succeeds. A host that cannot be reached is one line on
    stderr. Both end the command with exit status 1 otherwise.
    """
    try:
        return new_app(cls)
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return new_app(cls)
    except PhabfiveConnectionException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


__all__ = ["get_app", "new_app", "prompt_host"]
