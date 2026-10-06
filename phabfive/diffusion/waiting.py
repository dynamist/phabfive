# -*- coding: utf-8 -*-

"""Waiting for Diffusion to import what was nudged.

`diffusion.looksoon` only schedules an update, so whether it has happened
is a question asked again until the answer changes: a repository stops
reporting `isImporting`, or a commit is found with `isImported` set. Every
wait goes through `phabfive.retry._sleep`, which tests replace.
"""

import re
from time import monotonic

from phabfive import retry
from phabfive.commits import MIN_HASH_LENGTH
from phabfive.exceptions import PhabfiveInputException, PhabfiveTimeoutException

#: How long a wait lasts unless told otherwise, in seconds
DEFAULT_TIMEOUT = 300

# The first pause is short, because a nudged repository is often done in
# seconds, and the pauses double from there so a slow import is not asked
# about every second for minutes
FIRST_INTERVAL = 1.0
MAX_INTERVAL = 10.0

_HASH_RE = re.compile(r"^[0-9a-f]+$")


def validate_commit_hash(value):
    """The commit hash a wait is for, lower-cased, or an error saying why not.

    Only a hash: the repository is named separately, so a monogram such as
    ``rGUNNAR7d7fc2c3`` would say it twice and could say it differently.

    Raises
    ------
    PhabfiveInputException
        If the value is not hexadecimal, or is too short for Diffusion to
        match
    """
    commit = value.strip().lower()

    if not _HASH_RE.match(commit):
        raise PhabfiveInputException(
            f"'{value}' is not a commit hash; give the hash alone, the "
            "repository is named separately"
        )
    if len(commit) < MIN_HASH_LENGTH:
        raise PhabfiveInputException(
            f"'{value}' is too short; a commit hash needs at least "
            f"{MIN_HASH_LENGTH} characters"
        )

    return commit


def poll(check, timeout, describe):
    """Ask `check` until nothing is pending, pausing longer each time.

    Parameters
    ----------
    check : callable
        Takes nothing and returns what is still pending - empty when the
        wait is over. It may raise to end the wait early, for something
        that can never finish.
    timeout : float
        Seconds to keep asking for. `check` is always asked at least once,
        so a timeout of zero is a single look.
    describe : callable
        Takes what is still pending and returns the timeout's message

    Raises
    ------
    PhabfiveTimeoutException
        If something is still pending when the time is up, with it in
        `pending`
    """
    deadline = monotonic() + timeout
    interval = FIRST_INTERVAL

    while True:
        pending = check()

        if not pending:
            return

        remaining = deadline - monotonic()

        if remaining <= 0:
            raise PhabfiveTimeoutException(describe(pending), pending)

        retry._sleep(min(interval, remaining))
        interval = min(interval * 2, MAX_INTERVAL)


__all__ = ["DEFAULT_TIMEOUT", "poll", "validate_commit_hash"]
