# -*- coding: utf-8 -*-

"""Shared parsing for the ``--order`` option.

Result ordering is expressed as ``<field>[:asc|:desc]``. A bare field means
the direction people usually want -- highest or newest first for numbers and
dates, A-Z for names -- and the suffix is always accepted, so both directions
exist for every field and nothing is available only by implication.

The grammar is application-agnostic on purpose: each app supplies its own
field table and keeps the sort keys next to the data they read.
"""

from phabfive.exceptions import PhabfiveConfigException

DIRECTIONS = ("asc", "desc")

# Names Phorge itself uses in its "Order" dropdown and API. We do not accept
# them -- "outdated" is opaque and "newest" never says newest what -- but
# pointing at the phabfive spelling is kinder than listing every choice again.
ORDER_SUGGESTIONS = {
    "newest": "created",
    "oldest": "created:asc",
    "outdated": "updated:asc",
    "modified": "updated",
    "name": "title",
    "id": "created",
}


def _invalid(value, fields, hint=None):
    message = (
        f"Invalid order '{value}'. Valid fields: {', '.join(fields)}. "
        "Add ':asc' or ':desc' to pick a direction, e.g. 'updated:asc'."
    )
    if hint:
        message = f"{message} Did you mean '{hint}'?"
    return PhabfiveConfigException(message)


def parse_order(value, fields, default_directions, default=None):
    """
    Split an order expression into its field and direction.

    Parameters
    ----------
    value : str or None
        The user-supplied expression, e.g. ``"updated:asc"``. Empty or None
        falls back to `default`.
    fields : list
        Accepted field names, in the order they should be listed to the user.
    default_directions : dict
        Field name to the direction a bare field means. A value of None marks
        a field that takes no direction, such as a server-side relevance rank.
    default : str, optional
        The expression to use when `value` is empty.

    Returns
    -------
    tuple
        ``(field, direction)``, where direction is None for a directionless
        field.

    Raises
    ------
    PhabfiveConfigException
        If the field or direction is not recognised.
    """
    if value is None or not str(value).strip():
        if default is None:
            return None, None
        value = default

    raw = str(value).strip().lower()
    field, _, direction = raw.partition(":")

    if field not in default_directions or field not in fields:
        raise _invalid(raw, fields, ORDER_SUGGESTIONS.get(field))

    natural = default_directions[field]

    if natural is None:
        if direction:
            raise PhabfiveConfigException(
                f"Invalid order '{raw}'. '{field}' takes no direction; "
                f"use '{field}' on its own."
            )
        return field, None

    if not direction:
        return field, natural

    if direction not in DIRECTIONS:
        raise _invalid(raw, fields)

    return field, direction


__all__ = ["DIRECTIONS", "ORDER_SUGGESTIONS", "parse_order"]
