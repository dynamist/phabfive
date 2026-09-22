# -*- coding: utf-8 -*-
"""Instance configuration the command remembers between runs.

Completion is not the only thing that asks the same question over and over:
every `maniphest edit`, `create` and `search` asks maniphest.querystatuses,
and a script that edits in batches asks it once per batch. The answer changes
when somebody reconfigures Phorge, which is why it is cached like the lists
completion offers rather than fetched each time.

The library does not do this on its own - a program asks the server every
time, and never writes to the user's cache directory. The command opts its
apps in by handing them a CommandLookups, see phabfive.cli.apps.new_app.
Only a call site written to use the store reaches it, which keeps caching
opt-in per call site.
"""

import sys

from phabfive import cache

# Keyed the way completion keys its whole-instance lists, so that a namespace
# both use - the project icons - holds one entry rather than two
from phabfive.cli.completers import (
    PROJECT_ICON_CACHE_NAMESPACE,
    VALUES_CACHE_KEY,
    _fetch_project_icons,
)
from phabfive.constants import PROJECT_ICONS


class CommandLookups:
    """The lookup store the command gives its apps.

    Keyed by the app's own configuration rather than by reading it again,
    so an entry is filed under the instance the app is talking to.
    Best effort, like every cache operation: a lookup that cannot be read
    is a miss, and one that cannot be written is simply not remembered.
    """

    def __init__(self, conf):
        self._conf = conf

    def get(self, namespace):
        """Return what was remembered for namespace, or None."""
        directory, ttl = cache.context(namespace, conf=self._conf)
        value = cache.get(namespace, VALUES_CACHE_KEY, ttl=ttl, directory=directory)
        return None if value is cache.MISS else value

    def set(self, namespace, value):
        """Remember value for namespace."""
        directory, ttl = cache.context(namespace, conf=self._conf)
        cache.set(namespace, VALUES_CACHE_KEY, value, ttl=ttl, directory=directory)


def _known_project_icons(app):
    """Every icon phabfive can tell is valid on the instance, or None.

    The stock icons plus the ones projects carry, from the cache completion
    fills, or asked for and cached on a miss. None when the lookup failed,
    which is not a list to judge an icon by.
    """
    store = app.lookup_store
    icons = store.get(PROJECT_ICON_CACHE_NAMESPACE) if store is not None else None
    if isinstance(icons, list) and icons:
        return icons

    try:
        icons = _fetch_project_icons(app.phab)
    except Exception:
        return None

    if store is not None:
        store.set(PROJECT_ICON_CACHE_NAMESPACE, icons)
    return icons


def warn_unknown_project_icons(app, icons, allowed=()):
    """Warn about icons no project uses and Phorge does not ship.

    A warning rather than an error, because the icon set is instance
    configuration (projects.icons) that no Conduit method reports: an icon
    that is configured but that no project carries yet cannot be told from
    a typo. A search for one answers with nothing, and a dry run does not
    reach the server, which is why those two are where this is asked; a
    real write is checked by the server.

    Parameters
    ----------
    app : Phabfive
        The app, for its client and lookup store
    icons : list
        Icon keys as given, empty or None for none
    allowed : iterable, optional
        Further keys to accept, such as the milestone icon for a search
    """
    doubtful = [
        icon
        for icon in icons or []
        if icon not in PROJECT_ICONS and icon not in allowed
    ]
    if not doubtful:
        return

    known = _known_project_icons(app)
    if known is None:
        return

    for icon in doubtful:
        if icon not in known:
            sys.stderr.write(
                f"WARNING: No project uses the icon '{icon}' and it is not one "
                "Phorge ships, so it may be misspelled. An icon configured in "
                "projects.icons that no project uses yet cannot be checked.\n"
            )


__all__ = ["CommandLookups", "warn_unknown_project_icons"]
