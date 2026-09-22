# -*- coding: utf-8 -*-

"""Resolve what somebody typed to the project or user it names."""

import logging

from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.maniphest.resolvers import (
    PROJECT_PHID_PREFIX,
    ambiguous_project_message,
)

log = logging.getLogger(__name__)


def _search_one(phab, constraints, attachments=None):
    """The projects one project.search matches, or an error that says why not."""
    kwargs = {"constraints": constraints}

    if attachments:
        kwargs["attachments"] = attachments

    try:
        return (phab.project.search(**kwargs) or {}).get("data") or []
    except Exception as e:
        raise PhabfiveDataException(f"Failed to look up project: {e}")


def resolve_project(phab, ident, attachments=None):
    """Resolve a project identifier to exactly one project.

    Accepted, in the order they are tried:

    ==================  ==============================================
    ``#slug``           the project with that hashtag
    ``PHID-PROJ-...``   that project
    ``123``             the project with that ID
    ``slug``            a hashtag without its ``#``
    ``Name``            the one project with exactly that name
    ==================  ==============================================

    A name is tried last, and only an exact match counts, because a name is
    not unique: every milestone of a sprinting team is called something like
    "Sprint 1". Two matches are refused rather than one picked, with the ID
    of each so the caller can name the one they meant. There are no
    wildcards - an edit that could land on several projects is not what
    anybody typing one name meant.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    ident : str
        What was typed
    attachments : dict, optional
        project.search attachments to ask for on the way

    Returns
    -------
    dict
        The project.search result item

    Raises
    ------
    PhabfiveConfigException
        If nothing was typed
    PhabfiveDataException
        If no project matches, several do, or the lookup fails
    """
    value = (ident or "").strip()

    if not value:
        raise PhabfiveConfigException("No project given")

    if value.startswith("#"):
        found = _search_one(phab, {"slugs": [value[1:]]}, attachments)
        if not found:
            raise PhabfiveDataException(f"Project '{value}' does not exist")
        return found[0]

    if value.startswith(PROJECT_PHID_PREFIX):
        constraints = {"phids": [value]}
    elif value.isascii() and value.isdigit():
        constraints = {"ids": [int(value)]}
    else:
        constraints = None

    if constraints is not None:
        found = _search_one(phab, constraints, attachments)
        if not found:
            raise PhabfiveDataException(f"Project '{value}' does not exist")
        return found[0]

    # A hashtag typed without its "#". project.search normalises a slug the
    # way Phorge does, so "Human Resources" finds #human_resources here too.
    found = _search_one(phab, {"slugs": [value]}, attachments)
    if found:
        return found[0]

    # The name constraint matches on words rather than on the whole name, so
    # the exact match is picked out here.
    lowered = value.casefold()
    found = [
        project
        for project in _search_one(phab, {"name": value}, attachments)
        if (project.get("fields", {}).get("name") or "").casefold() == lowered
    ]

    if not found:
        raise PhabfiveDataException(f"Project '{value}' does not exist")

    if len(found) > 1:
        raise PhabfiveDataException(ambiguous_project_message(value, found))

    return found[0]
