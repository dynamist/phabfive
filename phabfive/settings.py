# -*- coding: utf-8 -*-
"""The names of the settings, and the names they had before.

``PHORGE_HOST`` names the instance and ``PHORGE_TOKEN`` authenticates
against it, with the rest of
``CONFIGURABLES`` beside them. They used to be ``PHAB_URL``, ``PHAB_TOKEN``
and ``PHAB_*``. Every old name still works, wherever a setting can be given -
the environment, a yaml file, a ``config`` dict - and is renamed to its
successor here, with a deprecation warning that says what to write instead.

The warning is logged once per process for each place and name, so a command
that reads its configuration more than once - the cache, the profiler and the
app each do - says it once. A reader that runs before the command has set
logging up, or whose output must stay clean, reads under ``quiet()``, and the
app's own read says what it would have. ``forget_warnings`` is for a test
suite, which runs many commands in one process.
"""

import contextlib
import logging
import os

from phabfive.constants import LEGACY_CONFIGURABLES

log = logging.getLogger(__name__)

__all__ = [
    "deprecation_message",
    "env_setting",
    "forget_warnings",
    "legacy_name",
    "modernize",
    "quiet",
    "warn_legacy",
    "warn_once",
]

# The order the names are listed in, which is the order a warning names them
_ORDER = {old: index for index, old in enumerate(LEGACY_CONFIGURABLES)}

#: (where, legacy name) pairs already warned about
_warned: set[tuple[str, str]] = set()
_quiet = 0


def legacy_name(name):
    """The deprecated name of the setting `name`, or None when it never had one."""
    for old, new in LEGACY_CONFIGURABLES.items():
        if new == name:
            return old
    return None


def _join(names):
    names = list(names)
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def deprecation_message(found, where):
    """One sentence naming the legacy keys in `found` and their successors.

    Parameters
    ----------
    found : iterable of str
        Legacy names that were given, e.g. ``["PHAB_URL", "PHAB_TOKEN"]``.
    where : str
        Where they were given, e.g. ``"the environment"`` or a file path.
    """
    found = sorted(set(found), key=_ORDER.get)
    successors = []
    for old in found:
        new = LEGACY_CONFIGURABLES[old]
        if new not in successors:
            successors.append(new)
    verb = "is" if len(found) == 1 else "are"
    return (
        f"{_join(found)} in {where} {verb} deprecated: use {_join(successors)} instead"
    )


def warn_legacy(found, where):
    """Warn once about each legacy name in `found` that was given in `where`.

    One warning names every one of them not warned about before, so a
    configuration with three old names earns one line, not three, and a
    second read of the same place earns none. Under `quiet()` nothing is
    logged or remembered, so the next reader outside it still warns.
    """
    if _quiet:
        return
    unseen = [old for old in found if (where, old) not in _warned]
    if not unseen:
        return
    _warned.update((where, old) for old in unseen)
    log.warning(deprecation_message(unseen, where))


def warn_once(where, message):
    """Warn `message` about `where` once per process, the way warn_legacy does.

    For a configuration problem found every time the configuration is read,
    which a command does more than once. Under `quiet()` nothing is logged
    or remembered, so the command's own read still warns.
    """
    if _quiet or (where, message) in _warned:
        return
    _warned.add((where, message))
    log.warning(message)


@contextlib.contextmanager
def quiet():
    """Read settings without warning about legacy names, and without remembering.

    For a reader that runs before the command has configured logging, where
    a warning would come out unformatted, or that runs during shell
    completion, where it would land in the terminal mid-keystroke. The
    command's own read of the configuration warns in its place.
    """
    global _quiet
    _quiet += 1
    try:
        yield
    finally:
        _quiet -= 1


def forget_warnings():
    """Forget which warnings were logged, so that they can be logged again."""
    _warned.clear()


def modernize(mapping):
    """Rename the legacy keys in `mapping` to their successors.

    Returns
    -------
    tuple
        ``(renamed, found)``: a new dict in which every ``PHAB_*`` key has
        become its ``PHORGE_*`` successor, and the legacy names that were
        there. A successor that is itself in `mapping` wins over its legacy
        name. Keys that are neither are kept as they are.
    """
    renamed = {
        key: value for key, value in mapping.items() if key not in LEGACY_CONFIGURABLES
    }
    found = sorted(
        (key for key in mapping if key in LEGACY_CONFIGURABLES), key=_ORDER.get
    )
    for old in found:
        new = LEGACY_CONFIGURABLES[old]
        if new in mapping:
            continue
        renamed[new] = mapping[old]
    return renamed, found


def env_setting(name, environ=None):
    """The environment's value for the setting `name`, or None when unset.

    For a reader that wants one setting straight from the environment
    without loading the configuration - the cache, the profiler - and must
    still honour the setting's legacy name, with the warning it earns.
    """
    environ = os.environ if environ is None else environ
    if name in environ:
        return environ[name]
    old = legacy_name(name)
    if old is not None and old in environ:
        warn_legacy([old], "the environment")
        return environ[old]
    return None
