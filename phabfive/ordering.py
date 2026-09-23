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
#
# These are maniphest's, which is the app that has the most of them; an app
# whose fields are spelled differently passes its own table as `suggestions`,
# because a hint has to point at a field that app actually has. The per-app
# tables live beside the field tables in `phabfive.constants`.
ORDER_SUGGESTIONS = {
    "newest": "created",
    "oldest": "created:asc",
    "outdated": "updated:asc",
    "modified": "updated",
    "name": "title",
    "id": "created",
}


def _invalid(value, fields, default_directions, hint=None):
    """The error for an order this app does not have.

    The example is one of *this app*'s own fields: the apps do not share a
    vocabulary - a project has no "updated" order and a paste has no
    "title" - so a fixed example would name a spelling the reader is about
    to be refused for.
    """
    example = next((field for field in fields if default_directions.get(field)), None)
    direction = f" Add ':asc' or ':desc' to pick a direction, e.g. '{example}:asc'."

    message = (
        f"Invalid order '{value}'. Valid fields: {', '.join(fields)}."
        f"{direction if example else ''}"
    )
    if hint:
        message = f"{message} Did you mean '{hint}'?"
    return PhabfiveConfigException(message)


def parse_order(value, fields, default_directions, default=None, suggestions=None):
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
    suggestions : dict, optional
        Phorge's own order names mapped to this app's spelling, for the "Did
        you mean" hint. Defaults to :data:`ORDER_SUGGESTIONS`, which is
        maniphest's; an app with different fields passes its own, so the hint
        never names a field that app does not have.

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

    table = ORDER_SUGGESTIONS if suggestions is None else suggestions

    if field not in default_directions or field not in fields:
        raise _invalid(raw, fields, default_directions, table.get(field))

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
        raise _invalid(raw, fields, default_directions)

    return field, direction


def sort_records(records, field, direction, keys):
    """
    Order search results the way the server was asked to order them.

    The server does the ordering -- an app sends the builtin order or the
    column vector for the field asked for, so a limit can be forwarded and
    still mean the top N. This sorts the records again anyway, for the two
    things a cursor cannot give: a stable tie-break, and a deterministic
    order over a fake or a partial page in a test.

    A field with no key here is left exactly as the server returned it,
    which is what ``relevance`` means: the response carries no rank field,
    so the server's ordering is the only one there is.

    Parameters
    ----------
    records : list
        Search result items, as the endpoint returned them.
    field : str or None
        The order field, as :func:`parse_order` answered.
    direction : str or None
        ``"asc"``, ``"desc"``, or None for a directionless field.
    keys : dict
        Field name to a sort key callable over one record. A field missing
        from it is not sorted here.

    Returns
    -------
    list
        A new list, in order.
    """
    key = keys.get(field)

    if key is None:
        return list(records)

    return sorted(records, key=key, reverse=direction == "desc")


def complete_order_value(incomplete, fields, default_directions):
    """
    Complete an order expression, fields first and directions after a ":".

    Shared by every command's ``--order`` completer so the three apps behave
    alike: a bare prefix completes the field names, and a prefix past the
    colon completes the two directions -- unless the field takes none, which
    completes nothing rather than offering a spelling that is refused.

    Pure string work over the app's own tables: it asks nothing of an
    instance and reads no configuration, so the command's completer is a
    one-line call.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed.
    fields : list
        The app's order fields, in the order they should be offered.
    default_directions : dict
        The app's field-to-natural-direction table, whose None entries mark
        the fields that take no direction.

    Returns
    -------
    list
        Matching completions.
    """
    if ":" in incomplete:
        field = incomplete.split(":", 1)[0]

        if not default_directions.get(field):
            # Unknown field, or one that takes no direction
            return []

        candidates = [f"{field}:{direction}" for direction in DIRECTIONS]
    else:
        candidates = list(fields)

    return [value for value in candidates if value.startswith(incomplete)]


__all__ = [
    "DIRECTIONS",
    "ORDER_SUGGESTIONS",
    "complete_order_value",
    "parse_order",
    "sort_records",
]
