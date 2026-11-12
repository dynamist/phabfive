# -*- coding: utf-8 -*-
"""Tasks, as the items of a spec.

Every value is written in the grammar the key takes on the way back in, not
the way `maniphest show` displays it: a status is its key (`resolved`, not
"Resolved"), a project its hashtag (`#security`, which names one project
where "Security" might name two), a policy its keyword. That is the whole
difference between this and a show command, and the reason this cannot be a
`--format`.

What a spec has no key for is not exported: points, custom fields, the
subtype, comments, the author. And three things are exported only when they
can be read back as written:

- **A priority** outside the stock table has no keyword the edit engine
  takes, so the key is left out. Absent means untouched, so nothing is lost
  on the way back.
- **A column** is exported for a task on exactly one workboard. `column:`
  names no board, so with several there is no saying which one it means.
- **A subscriber that is not a user** - a project, a package - is written as
  its PHID. It is kept as it is on the way back, but it cannot be added or
  removed through a spec.
- **A policy** the grammar has no spelling for - "Subscribers", "Task
  Author" - is left out, like the priority.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from phabfive.exceptions import PhabfiveNotFoundException
from phabfive.spec.edit import IDENTITY_KEYS, STAMP_KEY, format_stamp
from phabfive.spec.envelope import Kind

__all__ = ["export_tasks"]

#: How many objects one request asks about.
_CHUNK = 100

#: What `assignment:` says for a task nobody is assigned.
_NOBODY = "@none"

_RELATIONS = ("parents", "subtasks", "commits")


def _chunks(values: Sequence[Any]) -> list[Sequence[Any]]:
    return [values[start : start + _CHUNK] for start in range(0, len(values), _CHUNK)]


def _fetch(phab: Any, task_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The tasks, by id, with what an export reads off them."""
    found: dict[str, dict[str, Any]] = {}

    for chunk in _chunks([int(task_id) for task_id in task_ids]):
        result = phab.maniphest.search(
            constraints={"ids": list(chunk)},
            attachments={"columns": True, "projects": True, "subscribers": True},
        )

        for task in result.get("data") or []:
            found[str(task["id"])] = task

    missing = [f"T{task_id}" for task_id in task_ids if task_id not in found]

    if missing:
        raise PhabfiveNotFoundException(
            f"No such task: {', '.join(missing)}. A task the token may not "
            f"see is reported the same way"
        )

    return found


def _handles(phab: Any, phids: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    """What the instance calls each PHID. Raises rather than guessing."""
    handles: dict[str, Mapping[str, Any]] = {}

    for chunk in _chunks(sorted(set(phids))):
        found = phab.phid.query(phids=list(chunk))

        # A phabricator.Result maps like a dict without being one
        for phid, data in found.items():
            if hasattr(data, "get"):
                handles[phid] = data

    return handles


def _spelling(
    phid: str, handles: Mapping[str, Mapping[str, Any]], *, at: bool = False
) -> str:
    """One PHID as a spec writes it, or the PHID when it has no better name.

    Every spelling here is one the resolvers take back: a username, a
    project's `#hashtag`, a Space's or task's monogram, a commit's name. A
    PHID the token cannot name is written as the PHID, which they take too.
    """
    handle = handles.get(phid)

    if not handle:
        return phid

    kind = handle.get("type")
    name = handle.get("name") or ""
    last = (handle.get("uri") or "").rstrip("/").rsplit("/", 1)[-1]

    if kind == "USER" and name:
        return f"@{name}" if at else name

    if kind == "PROJ" and last:
        return f"#{last}"

    if kind == "SPCE" and last:
        return last

    if kind in ("TASK", "CMIT") and name:
        return name

    return phid


def _natural(text: str) -> tuple:
    """A sort key that puts T9 before T10."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", text)
        if part
    )


def _listed(phids: Sequence[str], handles: Mapping[str, Mapping[str, Any]]) -> list:
    """A list of PHIDs as a spec writes it, in an order that does not move.

    The instance answers in whatever order it holds them, which changes
    when a project is removed and added back. A document kept in a
    repository should differ when the object does and not otherwise, so the
    list is sorted: it is a set, and the order was never information.
    """
    return sorted({_spelling(phid, handles) for phid in phids}, key=_natural)


def _policy(value: Any, handles: Mapping[str, Mapping[str, Any]]) -> Any:
    """A policy as `visible-to:` takes it, or None for one it does not take.

    A keyword, `#project`, `@user` or a PHID. Phorge has policies outside
    that grammar - "Subscribers" and "Task Author" are stored as
    `obj.subscriptions.subscribers` and `obj.maniphest.author` - and a spec
    has no spelling for them, so the key is left out. Absent means
    untouched, so the task keeps the policy it has.
    """
    from phabfive.constants import POLICY_KEYWORDS

    if isinstance(value, str) and value.startswith("PHID-"):
        return _spelling(value, handles, at=True)

    return value if value in POLICY_KEYWORDS else None


def _priority(task: Mapping[str, Any]) -> Any:
    """The priority's keyword, or None for one the edit engine cannot set."""
    from phabfive.constants import PRIORITY_VALUES

    value = (task["fields"].get("priority") or {}).get("value")

    for keyword, number in PRIORITY_VALUES.items():
        if number == value:
            return keyword

    return None


def _column(task: Mapping[str, Any]) -> Any:
    """The column of a task on exactly one board, else None."""
    boards = task.get("attachments", {}).get("columns", {}).get("boards") or {}

    if len(boards) != 1:
        return None

    [board] = boards.values()
    columns = board.get("columns") or []

    return columns[0].get("name") if columns else None


def export_tasks(
    maniphest: Any, task_ids: Sequence[str], *, kind: Kind = Kind.EDIT
) -> list[dict[str, Any]]:
    """The spec items of the tasks `task_ids` name, in the order given.

    Parameters
    ----------
    maniphest : Maniphest
    task_ids : sequence of str
        Numeric ids, e.g. ``["123", "124"]``.
    kind : Kind, optional
        `Kind.EDIT` writes every key, the task's identity and its stamp, so
        the item states the whole task and a list that is empty is written
        as empty. `Kind.CREATE` writes what a new task like it would be
        created with: no identity, no stamp, no relations, and no key whose
        value is "nothing".

    Raises
    ------
    PhabfiveNotFoundException
        A task that does not exist, naming every one that does not.
    """
    from phabfive.maniphest.fetchers import fetch_edges_for_tasks

    phab = maniphest.phab
    tasks = _fetch(phab, task_ids)
    editing = kind is Kind.EDIT

    edges: Mapping[str, Mapping[str, list]] = {}

    if editing:
        edges = fetch_edges_for_tasks(
            phab, [task["phid"] for task in tasks.values()], _RELATIONS
        )

    named: list[str] = []

    for task in tasks.values():
        fields = task["fields"]
        attachments = task.get("attachments", {})

        named += [fields.get("ownerPHID"), fields.get("spacePHID")]
        named += list((fields.get("policy") or {}).values())
        named += attachments.get("projects", {}).get("projectPHIDs") or []
        named += attachments.get("subscribers", {}).get("subscriberPHIDs") or []

        for relation in _RELATIONS:
            named += edges.get(task["phid"], {}).get(relation) or []

    handles = _handles(
        phab,
        [phid for phid in named if isinstance(phid, str) and phid.startswith("PHID-")],
    )

    items = []

    for task_id in task_ids:
        task = tasks[task_id]
        fields = task["fields"]
        attachments = task.get("attachments", {})
        policy = fields.get("policy") or {}
        item: dict[str, Any] = {}

        def write(key: str, value: Any) -> None:
            """Every key of an edit; only the ones that say something of a create."""
            if value is None:
                return

            if not editing and value in ([], "", _NOBODY):
                return

            item[key] = value

        if editing:
            item[IDENTITY_KEYS["task"]] = f"T{task['id']}"
            item[STAMP_KEY] = format_stamp(fields.get("dateModified") or 0)

        write("title", fields.get("name") or "")
        write("status", (fields.get("status") or {}).get("value"))
        write("priority", _priority(task))

        owner = fields.get("ownerPHID")
        write("assignment", _spelling(owner, handles) if owner else _NOBODY)

        write(
            "projects",
            _listed(attachments.get("projects", {}).get("projectPHIDs") or [], handles),
        )
        write("column", _column(task))
        write(
            "subscribers",
            _listed(
                attachments.get("subscribers", {}).get("subscriberPHIDs") or [],
                handles,
            ),
        )

        space = fields.get("spacePHID")
        write("space", _spelling(space, handles) if space else None)

        write("visible-to", _policy(policy.get("view"), handles))
        write("editable-by", _policy(policy.get("edit"), handles))

        if editing:
            for relation in _RELATIONS:
                write(
                    relation,
                    _listed(edges.get(task["phid"], {}).get(relation) or [], handles),
                )

        # Last: it is the long one, and everything above it fits on a screen
        write("description", (fields.get("description") or {}).get("raw") or "")

        items.append(item)

    return items
