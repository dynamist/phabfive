# -*- coding: utf-8 -*-

"""Selecting a repository's URIs by what they are, for `uri list`.

A repository accumulates URIs - the built-in ones Phorge generates per
protocol, plus whatever was added to observe or mirror somewhere else - and
the question a reader arrives with is rarely "all of them". #33 asked for
the visible ones and #32 for the observed and mirrored ones; both are this
module, answered against the API record rather than the formatted one, so
that a URI filtered away costs no credential lookup.

Matching an I/O or display value reads **both** ``raw`` and ``effective``,
because those are two true and different answers to "is this URI an observe
URI": ``effective`` is what is in force, and ``raw`` is what is written on
the URI, which is where the literal "default" lives. ``--io=default`` would
otherwise match nothing at all, "default" never being an effective value.
"""

from phabfive.diffusion.formatters import uri_origin


def _matches_value(section, value):
    """Whether a resolved section is ``value``, as set or as in force.

    Parameters
    ----------
    section : dict
        An ``io`` or ``display`` section of a URI record
    value : str
        The value asked for, already resolved through its aliases

    Returns
    -------
    bool
    """
    section = section or {}

    return value in (section.get("raw"), section.get("effective"))


def select_uris(
    uris, clone_only=False, io=None, display=None, builtin=None, disabled=None
):
    """Keep the URIs that answer to every filter given.

    Filters combine with AND, and a filter left out is not applied - so no
    argument at all is every URI, which is what `uri list` has always
    printed.

    Parameters
    ----------
    uris : list
        URI records, as the "uris" attachment returns them
    clone_only : bool, optional
        Keep only the URIs the instance shows as clone URIs. This is
        ``display.effective == "always"``, which is `--clone`, kept as its
        own argument because it predates the rest.
    io : str, optional
        Keep the URIs whose I/O is this, set or in force
    display : str, optional
        Keep the URIs whose display is this, set or in force
    builtin : bool, optional
        True keeps the built-in URIs, False the external ones
    disabled : bool, optional
        True keeps the disabled URIs, False the enabled ones

    Returns
    -------
    list
        The matching URI records, in the order they arrived
    """

    def keep(uri):
        fields = uri.get("fields", {})

        if clone_only and (fields.get("display") or {}).get("effective") != "always":
            return False

        if io is not None and not _matches_value(fields.get("io"), io):
            return False

        if display is not None and not _matches_value(fields.get("display"), display):
            return False

        if builtin is not None and (uri_origin(uri) == "built-in") != builtin:
            return False

        if disabled is not None and bool(fields.get("disabled")) != disabled:
            return False

        return True

    return [uri for uri in uris if keep(uri)]
