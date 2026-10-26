# -*- coding: utf-8 -*-

"""The time grammar a spec and a search flag both speak.

``created-after: 7d`` in a spec and ``--created-after 7d`` on the command
line are the same value, so they have to be read by the same parser. This is
that parser, moved out of ``phabfive/maniphest/utils.py`` for one reason:
``phabfive.spec.validate`` has to read a time offline, and importing
``phabfive.maniphest`` would pull in ``phabfive.core`` and the ``phabricator``
client onto the one code path whose whole claim is that it needs neither.

Standard library only, exactly like ``phabfive.spec.registry``. It moved
out of ``phabfive.maniphest.utils`` rather than being re-exported from it,
the same way the Jinja2 engine did in #471: two import paths for one
function is how a grammar ends up with two readers that disagree.

The grammar, said once::

    time     := relative | absolute
    relative := <number> [ <unit> ]
    unit     := h | d | w | m | y        (case-insensitive; d when omitted)
    absolute := YYYY-MM-DD [ (T | " ") HH:MM [ :SS ] ]

A relative time counts back from now: ``created-after: 7d`` is "created in
the last seven days". An absolute one is a moment in **local time**, a bare
date meaning its midnight: ``created-after: 2026-07-01`` is "created on or
after the first of July", and ``created-before: 2026-08-01`` "created before
the first of August". Local rather than UTC because a calendar period is
somebody's calendar, and :mod:`phabfive.maniphest.stats` buckets by the same
clock. There is no offset or ``Z``: the date a person types is the one their
calendar shows.

``phabfive.spec.schema.TIME_PATTERN`` publishes that grammar as a regular
expression for editors and other tools. It is deliberately narrower than
this parser, which also accepts whatever :func:`float` happens to read -
``1e3``, ``nan``, ``1_0``. Those are accidents of the backward-compatible
"a bare number is days" branch, not part of the format, so the published
pattern does not bless them; what matters is that nothing phabfive would
accept is ever *reported* as a bad time, which is why the offline pass calls
this function rather than matching that pattern.
"""

import datetime
import re
import time

from phabfive.exceptions import PhabfiveInputException

__all__ = [
    "TIME_UNITS",
    "parse_absolute_time",
    "parse_time",
    "parse_time_with_unit",
]

#: Each unit suffix and what one of it is worth in days. Months and years are
#: the approximations the search has always used.
TIME_UNITS = {
    "h": 1 / 24,  # hours to days
    "d": 1,  # days
    "w": 7,  # weeks to days
    "m": 30,  # months to days (approximate)
    "y": 365,  # years to days (approximate)
}

_WITH_UNIT = re.compile(r"^(-?[0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)$")

#: Anything starting like a date is read as one, so ``2026-13-01`` is called
#: a bad date rather than a bad number of days.
_LOOKS_ABSOLUTE = re.compile(r"^[0-9]{4}-")

_ABSOLUTE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"(?:[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?)?$"
)


def _now():
    """The current UNIX time, one place for a test to stop the clock."""
    return time.time()


def parse_absolute_time(time_value):
    """
    Parse an absolute date or date and time, in local time.

    Parameters
    ----------
    time_value : str, int, float, datetime.date, or None
        ``YYYY-MM-DD``, ``YYYY-MM-DDTHH:MM`` or ``YYYY-MM-DD HH:MM:SS``. A
        program may hand in a `datetime.datetime`, which is returned as it
        is, or a `datetime.date`, which is its midnight.

    Returns
    -------
    datetime.datetime or None
        A naive datetime in local time, or None when the value is not
        written as a date at all - which is how a caller tells a relative
        time from an absolute one.

    Raises
    ------
    PhabfiveInputException
        It is written as a date and is not one, e.g. ``2026-02-30``.

    Examples
    --------
    >>> parse_absolute_time("2026-07-01")
    datetime.datetime(2026, 7, 1, 0, 0)
    >>> parse_absolute_time("7d") is None
    True
    """
    if isinstance(time_value, datetime.datetime):
        return time_value

    if isinstance(time_value, datetime.date):
        return datetime.datetime.combine(time_value, datetime.time())

    if time_value is None or isinstance(time_value, (bool, int, float)):
        return None

    time_str = str(time_value).strip()

    if not _LOOKS_ABSOLUTE.match(time_str):
        return None

    match = _ABSOLUTE.match(time_str)
    if not match:
        raise PhabfiveInputException(
            f"Invalid date: '{time_value}'. Expected YYYY-MM-DD, optionally "
            f"followed by a time as HH:MM or HH:MM:SS, e.g. '2026-07-01' or "
            f"'2026-07-01T09:30'"
        )

    try:
        return datetime.datetime(*(int(part or 0) for part in match.groups()))
    except ValueError as e:
        raise PhabfiveInputException(f"Invalid date: '{time_value}': {e}")


def parse_time(time_value):
    """
    Parse any TIME value into the UNIX second it names.

    The one reader every ``*-after``/``*-before`` filter goes through: a
    relative time is that long before now, an absolute one is that moment in
    local time.

    Parameters
    ----------
    time_value : str, int, float, datetime.date, or None
        ``7d``, ``2w``, ``14``, ``2026-07-01`` or ``2026-07-01T09:30``, or a
        `datetime.datetime` or `datetime.date` from a program. A naive one
        is local time.

    Returns
    -------
    int or None
        Epoch seconds, or None if input is None.

    Raises
    ------
    PhabfiveInputException
        It is neither a relative nor an absolute time.
    """
    if time_value is None:
        return None

    absolute = parse_absolute_time(time_value)
    if absolute is not None:
        return int(absolute.timestamp())

    days = parse_time_with_unit(time_value)
    return int(_now()) - round(days * 24 * 3600)


def parse_time_with_unit(time_value):
    """
    Parse time value with optional unit suffix.

    Supports the following time units:
    - h: hours
    - d: days (default when no unit specified)
    - w: weeks (7 days)
    - m: months (30 days)
    - y: years (365 days)

    Parameters
    ----------
    time_value : str, int, float, or None
        Time value with optional unit suffix.
        Examples: "1w", "2m", "7d", "7", 7

    Returns
    -------
    float or None
        Number of days as a float, or None if input is None.

    Raises
    ------
    PhabfiveInputException
        If the format is invalid, the unit is not recognized, or the value is
        negative.

    Examples
    --------
    >>> parse_time_with_unit("1w")
    7.0
    >>> parse_time_with_unit("2m")
    60.0
    >>> parse_time_with_unit("7")
    7.0
    >>> parse_time_with_unit(7)
    7.0
    >>> parse_time_with_unit("1h")
    0.041666666666666664
    """
    if time_value is None:
        return None

    # Convert to string for parsing
    time_str = str(time_value).strip()

    if not time_str:
        raise PhabfiveInputException("Time value cannot be empty")

    # Try to parse as a plain number first (backward compatibility)
    try:
        # If it's just a number, treat as days
        days = float(time_str)
        if days < 0:
            raise PhabfiveInputException(
                f"Time value cannot be negative: '{time_value}'"
            )
        return days
    except ValueError as e:
        # If it's a negative value error, re-raise it
        if "cannot be negative" in str(e):
            raise
        # Not a plain number, try to parse with unit suffix
        pass

    # Extract numeric part and unit suffix
    match = _WITH_UNIT.match(time_str)
    if not match:
        raise PhabfiveInputException(
            f"Invalid time format: '{time_value}'. "
            f"Expected format: NUMBER[UNIT] where UNIT is one of: h, d, w, m, y. "
            f"Examples: '7d', '1w', '2m', '1y', '12h', or just '7' (defaults to days), "
            f"or a date such as '2026-07-01'"
        )

    numeric_part = match.group(1)
    unit = match.group(2).lower()

    if unit not in TIME_UNITS:
        valid_units = ", ".join(sorted(TIME_UNITS.keys()))
        raise PhabfiveInputException(
            f"Invalid time unit: '{unit}'. Valid units are: {valid_units}"
        )

    try:
        numeric_value = float(numeric_part)
    except ValueError:
        raise PhabfiveInputException(f"Invalid numeric value: '{numeric_part}'")

    if numeric_value < 0:
        raise PhabfiveInputException(f"Time value cannot be negative: '{time_value}'")

    # Convert to days
    days = numeric_value * TIME_UNITS[unit]
    return days
