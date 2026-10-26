# -*- coding: utf-8 -*-
"""Tasks created and closed per period, counted from two searches (#598).

``maniphest stats`` answers "how is this project trending" from exactly two
``maniphest.search`` walks over the whole range - one constrained by
``createdStart``/``createdEnd``, one by ``closedStart``/``closedEnd`` - and
buckets what comes back here, by each task's ``dateCreated`` and
``dateClosed``. One query per period would cost a round trip per week and
say nothing more.

Everything in this module is data in, data out: no request, no printing,
standard library only. `Maniphest.task_stats` does the fetching and hands
the raw search items to :func:`build_stats`.

The rules, said once:

- **Periods** are calendar periods in local time: ISO weeks starting on
  Monday (``2026-W27``), calendar months (``2026-07``) and quarters
  (``2026-Q3``). The first and last period are clipped to the range asked
  for, and every period in the range has a record, an empty one included,
  so a series has no holes.
- **A task counts once** per period and group, however many of the matched
  projects it is tagged with: items are de-duplicated by PHID.
- **Closed** counts only the statuses it is handed - by default the
  instance's resolved status, so Invalid, Duplicate and Wontfix do not
  inflate the throughput. ``Net`` is ``Created - Closed`` as counted.
- **Reopened tasks.** ``dateClosed`` is the most recent close, so a task
  closed, reopened and closed again counts once, in the period of its last
  close; a task reopened and still open has no ``dateClosed`` and is not
  counted as closed at all.
- **Time to close** is ``dateClosed - dateCreated`` of the tasks counted as
  closed in the period, in days, reported as the median and the 85th
  percentile. Not the mean, which a few very old tasks skew.
- **Points** are left out entirely, rather than reported as 0, when no task
  in either answer carries any - which is what an instance with points
  turned off answers with.
"""

import datetime
import math
from collections import defaultdict

from phabfive.exceptions import PhabfiveInputException

__all__ = [
    "GROUPINGS",
    "NO_GROUP",
    "PERIODS",
    "build_stats",
    "percentile",
    "period_of",
    "periods_between",
]

#: The period lengths `--period` takes.
PERIODS = ("week", "month", "quarter")

#: What `--by` splits each period by, and the record key the group is
#: published under.
GROUPINGS = {
    "owner": "Owner",
    "project": "Project",
    "subtype": "Subtype",
    "closer": "Closer",
}

#: The group of a task that has no owner, no project or no closer.
NO_GROUP = "(none)"

_DAY = 86400


def _check_period(period):
    if period not in PERIODS:
        raise PhabfiveInputException(
            f"Invalid period: '{period}'. Expected one of: {', '.join(PERIODS)}"
        )


def period_of(moment, period):
    """The calendar period a local datetime falls in.

    Parameters
    ----------
    moment : datetime.datetime
        A naive datetime in local time.
    period : str
        One of :data:`PERIODS`.

    Returns
    -------
    tuple
        ``(label, start, end)``: the label, e.g. ``2026-W27``, and the local
        datetimes the period starts at and ends before.
    """
    _check_period(period)

    day = moment.date()

    if period == "week":
        year, week, weekday = day.isocalendar()
        first = day - datetime.timedelta(days=weekday - 1)
        label = f"{year}-W{week:02d}"
        following = first + datetime.timedelta(days=7)
    elif period == "month":
        first = day.replace(day=1)
        label = f"{first.year}-{first.month:02d}"
        following = (first + datetime.timedelta(days=32)).replace(day=1)
    else:
        quarter = (day.month - 1) // 3 + 1
        first = datetime.date(day.year, 3 * quarter - 2, 1)
        label = f"{first.year}-Q{quarter}"
        following = (first + datetime.timedelta(days=95)).replace(day=1)

    start = datetime.datetime.combine(first, datetime.time())
    end = datetime.datetime.combine(following, datetime.time())

    return label, start, end


def periods_between(since, until, period):
    """Every period from `since` up to, and not including, `until`.

    Parameters
    ----------
    since, until : datetime.datetime
        Naive local datetimes. `until` is exclusive.
    period : str
        One of :data:`PERIODS`.

    Returns
    -------
    list of tuple
        ``(label, start, end)`` for each period, with the first start and
        the last end clipped to the range.
    """
    _check_period(period)

    found = []
    cursor = since

    while cursor < until:
        label, _, end = period_of(cursor, period)
        found.append((label, cursor, min(end, until)))
        cursor = end

    return found


def percentile(values, fraction):
    """The `fraction` quantile of `values`, linearly interpolated.

    The interpolation between closest ranks that spreadsheets call
    PERCENTILE.INC and numpy calls "linear": the median of 1, 2, 3, 4 is
    2.5. None for no values at all.
    """
    if not values:
        return None

    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return float(ordered[lower])

    weight = position - lower

    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _unique(items):
    """Search items de-duplicated by PHID, in the order first seen."""
    seen = {}

    for item in items:
        key = item.get("phid") or item.get("id")
        seen.setdefault(key, item)

    return list(seen.values())


def _epoch(fields, key):
    value = fields.get(key)

    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _points(fields):
    value = fields.get("points")

    if value is None or value == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status(fields):
    status = fields.get("status")

    if isinstance(status, dict):
        return status.get("value")

    return status


def _groups(item, by, names):
    """The groups one task counts in. A task in two projects counts in both."""
    fields = item.get("fields") or {}

    if by == "owner":
        phids = [fields.get("ownerPHID")]
    elif by == "closer":
        phids = [fields.get("closerPHID")]
    elif by == "subtype":
        return [fields.get("subtype") or NO_GROUP]
    else:
        projects = (item.get("attachments") or {}).get("projects") or {}
        phids = list(projects.get("projectPHIDs") or [])

    groups = sorted({names.get(phid, phid) for phid in phids if phid})

    return groups or [NO_GROUP]


def _round(value):
    return None if value is None else round(value, 2)


def _day_label(moment):
    return moment.date().isoformat()


def build_stats(
    created,
    closed,
    *,
    since,
    until,
    period="week",
    by=None,
    closed_statuses=None,
    names=None,
):
    """The per-period records, from the two searches' raw items.

    Parameters
    ----------
    created : iterable of dict
        ``maniphest.search`` items found by creation date in the range.
    closed : iterable of dict
        ``maniphest.search`` items found by close date in the range.
    since, until : datetime.datetime
        The range, as naive local datetimes. `until` is exclusive. An item
        outside it is ignored, whatever the server answered.
    period : str
        One of :data:`PERIODS`.
    by : str or None
        One of :data:`GROUPINGS`, or None for one record per period.
    closed_statuses : iterable of str or None
        The status values that count as closed. None counts every task that
        has a ``dateClosed``.
    names : mapping or None
        PHID to the name a group is published under, for ``owner``,
        ``closer`` and ``project``. A PHID missing from it is published as
        it is.

    Returns
    -------
    list of dict
        One record per period, or per period and group with `by`, in
        period order and then group order.
    """
    _check_period(period)

    if by is not None and by not in GROUPINGS:
        raise PhabfiveInputException(
            f"Invalid grouping: '{by}'. Expected one of: {', '.join(GROUPINGS)}"
        )

    names = names or {}
    counted = None if closed_statuses is None else set(closed_statuses)
    created = _unique(created)
    closed = _unique(closed)

    since_epoch = since.timestamp()
    until_epoch = until.timestamp()
    spans = periods_between(since, until, period)

    def bucket(epoch):
        if epoch is None or not since_epoch <= epoch < until_epoch:
            return None
        return period_of(datetime.datetime.fromtimestamp(epoch), period)[0]

    def groups_of(item):
        return _groups(item, by, names) if by else [None]

    with_points = any(
        _points(item.get("fields") or {}) is not None for item in created + closed
    )

    tally = defaultdict(
        lambda: {"created": 0, "closed": 0, "added": 0.0, "done": 0.0, "days": []}
    )
    seen_groups = set()

    for item in created:
        fields = item.get("fields") or {}
        label = bucket(_epoch(fields, "dateCreated"))
        if label is None:
            continue

        for group in groups_of(item):
            seen_groups.add(group)
            entry = tally[(label, group)]
            entry["created"] += 1
            entry["added"] += _points(fields) or 0.0

    for item in closed:
        fields = item.get("fields") or {}
        closed_at = _epoch(fields, "dateClosed")
        label = bucket(closed_at)
        if label is None:
            continue
        if counted is not None and _status(fields) not in counted:
            continue

        created_at = _epoch(fields, "dateCreated")
        days = None
        if created_at is not None and closed_at is not None:
            days = max(closed_at - created_at, 0) / _DAY

        for group in groups_of(item):
            seen_groups.add(group)
            entry = tally[(label, group)]
            entry["closed"] += 1
            entry["done"] += _points(fields) or 0.0
            if days is not None:
                entry["days"].append(days)

    groups = sorted(seen_groups, key=lambda g: (g == NO_GROUP, str(g).lower()))
    if not by:
        groups = [None]

    records = []

    for label, start, end in spans:
        for group in groups:
            entry = tally.get((label, group)) or {
                "created": 0,
                "closed": 0,
                "added": 0.0,
                "done": 0.0,
                "days": [],
            }

            record = {"Period": label}
            if by:
                record[GROUPINGS[by]] = group
            record["Start"] = _day_label(start)
            # The last day the period covers, so a week reads Monday-Sunday
            record["End"] = _day_label(end - datetime.timedelta(seconds=1))
            record["Created"] = entry["created"]
            record["Closed"] = entry["closed"]
            record["Net"] = entry["created"] - entry["closed"]
            if with_points:
                record["PointsAdded"] = _round(entry["added"])
                record["PointsCompleted"] = _round(entry["done"])
            record["MedianDaysToClose"] = _round(percentile(entry["days"], 0.5))
            record["P85DaysToClose"] = _round(percentile(entry["days"], 0.85))

            records.append(record)

    return records
