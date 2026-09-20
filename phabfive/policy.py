# -*- coding: utf-8 -*-
"""The policy grammar every application shares.

Phorge spells a policy either as one of a handful of keyword constants or as
a PHID - a project, a user, or a custom rule. This module is what turns what
somebody types into one of those, and what turns one of those back into
something a person can read:

==================  ====================================================
``public``          passed through as the Phorge keyword; likewise
``users``           ``admin`` and ``no-one``
``#projectslug``    ``project.search`` -> ``PHID-PROJ-...``
``@username``       ``user.search`` -> ``PHID-USER-...``
``PHID-...``        passed through
==================  ====================================================

It is deliberately application-agnostic: repository policies are the first
caller, task policies the next, and neither one owns the grammar.

Anything outside that grammar is refused here, before a call is made, because
the API cannot be relied on to notice. Conduit reads an unrecognised policy
value as a policy nobody satisfies, so ``--view=nonsense`` comes back as the
same self-lockout validation error as ``--view=no-one`` - which would report a
typo as a permissions problem.
"""

# python std lib
import logging

# phabfive imports
from phabfive.constants import POLICY_KEYWORDS, POLICY_LABELS
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException

log = logging.getLogger(__name__)

PHID_PREFIX = "PHID-"

# What a policy option takes, said once so every error message and every
# --help string says it the same way
POLICY_GRAMMAR = f"{', '.join(POLICY_KEYWORDS)}, #project, @user, or a PHID"


def validate_policy_value(value, option=None):
    """Refuse a policy value no lookup could resolve, before anything is sent.

    Only the shape is checked - that the value is a keyword, a PHID, or
    prefixed with # or @. Whether that project or user exists is a question
    for resolve_policy_value, which needs the API to answer it.

    Parameters
    ----------
    value : str or None
        What was typed. None is "not asked for" and is accepted.
    option : str, optional
        The option it came from, named in the error message

    Returns
    -------
    str or None
        The value, stripped of surrounding whitespace

    Raises
    ------
    PhabfiveConfigException
        If the value is outside the grammar
    """
    if value is None:
        return None

    value = value.strip()

    if value in POLICY_KEYWORDS or value.startswith(PHID_PREFIX):
        return value

    if value[:1] in ("#", "@") and len(value) > 1:
        return value

    where = f"{option} " if option else ""
    raise PhabfiveConfigException(
        f"{where}must be one of: {POLICY_GRAMMAR} (got '{value}')"
    )


def resolve_policy_value(phab, value, option=None):
    """Resolve one policy value to what Conduit accepts.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    value : str or None
        A keyword, #projectslug, @username or PHID. None is passed through.
    option : str, optional
        The option it came from, named in any error message

    Returns
    -------
    str or None
        A Phorge policy keyword or a PHID

    Raises
    ------
    PhabfiveConfigException
        If the value is outside the grammar
    PhabfiveDataException
        If the project or user it names does not exist
    """
    value = validate_policy_value(value, option=option)

    if value is None or value in POLICY_KEYWORDS or value.startswith(PHID_PREFIX):
        return value

    if value.startswith("#"):
        return _resolve_project(phab, value[1:])

    return _resolve_user(phab, value[1:])


def _resolve_project(phab, slug):
    """The PHID of the project a hashtag names.

    Matched on slugs rather than names, because a hashtag is unique and a
    name is not: several milestones can be called "Sprint 1", and a policy
    takes exactly one project.
    """
    try:
        result = phab.project.search(constraints={"slugs": [slug]})
    except Exception as e:
        raise PhabfiveDataException(f"Failed to resolve project '#{slug}': {e}")

    data = (result or {}).get("data") or []

    if not data:
        raise PhabfiveDataException(f"Project '#{slug}' does not exist")

    return data[0]["phid"]


def _resolve_user(phab, username):
    """The PHID of the user a name names."""
    try:
        result = phab.user.search(constraints={"usernames": [username]})
    except Exception as e:
        raise PhabfiveDataException(f"Failed to resolve user '@{username}': {e}")

    data = (result or {}).get("data") or []

    if not data:
        raise PhabfiveDataException(f"User '@{username}' does not exist")

    return data[0]["phid"]


def resolve_policy_names(phab, values):
    """Name the PHIDs among a set of policy values.

    Best effort, always: naming a policy is a convenience, and a read must
    not fail because phid.query did not answer.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    values : iterable
        Policy values, keywords and PHIDs mixed; anything that is not a PHID
        is ignored

    Returns
    -------
    dict
        PHID to the name to show for it. Missing for anything the instance
        would not name, which policy_label then leaves as the PHID.
    """
    phids = sorted(
        {
            value
            for value in values
            if isinstance(value, str) and value.startswith(PHID_PREFIX)
        }
    )

    if not phids:
        return {}

    # phid.query answers with a phabricator.Result, which maps like a dict
    # without being one - so the answer is read by asking it for items()
    # rather than by isinstance, which would quietly name nothing at all.
    # That read is inside the try for the same reason the call is: naming a
    # policy is a convenience, and nothing here may fail a read.
    try:
        found = phab.phid.query(phids=phids)

        return {
            phid: _handle_name(phid, data)
            for phid, data in found.items()
            if hasattr(data, "get")
        }
    except Exception as e:
        log.warning(f"Failed to resolve policy PHIDs: {e}")
        return {}


def _handle_name(phid, data):
    """How one policy PHID reads to a person.

    A project and a user are named in the same spelling the grammar above
    takes, so what a policy is shown as can be typed straight back in - which
    a bare "Infrastructure" could not be, there being no option that accepts
    a project's display name. phid.query carries a project's slug only in its
    URI, the way it carries a Space's monogram there; a user's ``name`` is
    already the username.

    Anything else - a custom policy rule, most often - is whatever the
    instance calls it, and the PHID when it calls it nothing.
    """
    kind = data.get("type")
    name = data.get("name") or data.get("fullName") or ""
    slug = (data.get("uri") or "").rstrip("/").rsplit("/", 1)[-1]

    if kind == "PROJ" and slug:
        return f"#{slug}"

    if kind == "USER" and (name or slug):
        return f"@{name or slug}"

    return name or phid


def policy_label(value, names=None):
    """Label one policy value the way phabfive shows it.

    Parameters
    ----------
    value : str or None
        A policy keyword or PHID, as a record reports it
    names : dict, optional
        PHID to name, from resolve_policy_names

    Returns
    -------
    str
        The web UI's label for a keyword, the resolved name for a PHID that
        was resolved, and the raw value for anything else - inventing a name
        for a policy is worse than showing the PHID.
    """
    if value is None:
        return "(none)"

    if value in POLICY_LABELS:
        return POLICY_LABELS[value]

    if names and value in names:
        return names[value]

    return value


def policy_lockout_message(error):
    """The sentence to report when Phorge refused an edit as a self-lockout.

    Phorge will not let you apply a policy that takes the object away from
    you, and says so as a validation error inside an APIError:

        Validation errors:
          - The view policy of this object would no longer allow you to view
            the object.

    Worth catching, because the stack trace around it says nothing the
    sentence does not, and because an unknown keyword arrives here too - the
    API cannot tell "nonsense" from "no-one", which is why the grammar is
    checked before the call rather than after it.

    Parameters
    ----------
    error : Exception or str
        What the API raised

    Returns
    -------
    str or None
        The sentence to report, or None when the error is something else and
        should be reported as it stands
    """
    text = str(error)

    if "no longer allow you" not in text:
        return None

    reasons = [
        line.strip().lstrip("-").strip()
        for line in text.splitlines()
        if "no longer allow you" in line
    ]

    detail = " ".join(reasons) or text.strip()

    return f"{detail} Nothing was changed; choose a policy that still includes you."


__all__ = [
    "POLICY_GRAMMAR",
    "policy_label",
    "policy_lockout_message",
    "resolve_policy_names",
    "resolve_policy_value",
    "validate_policy_value",
]
