# -*- coding: utf-8 -*-
"""The vocabulary of an edit document, and nothing that needs a server.

A create document describes objects that do not exist yet. An edit document
addresses ones that do: each item names its object - ``task: T123`` - and
states what it should be. What is stated is compared with the object as the
instance has it, and only the difference is sent.

Three rules decide what an item means, and this module is where they are
written down once so the offline pass, the planner and the exporter cannot
disagree about them:

- **A key that is absent is left alone.** So is one written with nothing
  after it. ``{task: T123, status: resolved}`` is a whole edit.
- **A list is the full set, or a delta.** ``[alice, bob]`` is who the
  subscribers should be; ``["+carol", "-bob"]`` adds one and removes one and
  leaves the rest. One list is one or the other, never both.
- **A value is a state, not a move.** ``priority: raise`` and
  ``column: forward`` describe a change relative to wherever the object is,
  so applying the document twice would move it twice. They are refused.

Standard library and `phabfive.exceptions` only, like the registry: this is
read by the offline pass, which runs with no token and no network.
"""

import dataclasses
import datetime
from typing import Any, Mapping

from phabfive.exceptions import PhabfiveInputException

__all__ = [
    "ADD",
    "CREATE_ONLY_KEYS",
    "EDIT_OBJECT_KEYS",
    "IDENTITY_KEYS",
    "REMOVE",
    "RELATIVE_VALUES",
    "STAMP_KEY",
    "ListEdit",
    "MixedListError",
    "format_stamp",
    "list_entries",
    "parse_list_edit",
    "parse_stamp",
]

#: The body keys of an edit document and the object type each holds, in the
#: order a document is read. A type joins when its planner does.
EDIT_OBJECT_KEYS: tuple[tuple[str, str], ...] = (("tasks", "task"),)

#: The key that names the object an item edits, per object type. Not `id:`,
#: which is a create document's name for an object *inside the document*.
IDENTITY_KEYS: Mapping[str, str] = {"task": "task"}

#: When the object was last modified, as the document's writer saw it.
STAMP_KEY = "modified"

#: The values that name a change rather than a result, per object type and
#: key. `phabfive edit` takes them; a document that states what an object
#: should be cannot.
RELATIVE_VALUES: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "task": {
        "priority": ("raise", "higher", "lower"),
        "column": ("forward", "backward"),
    },
}

#: The keys of a create item that an edit item does not have, with what to
#: write instead. They are reported by name rather than as a misspelling.
CREATE_ONLY_KEYS: Mapping[str, Mapping[str, str]] = {
    "task": {
        "id": (
            "id: names an object inside a create document. An edit names the "
            "task itself: task: T123"
        ),
        "tasks": (
            "An edit document does not nest. Write each task as an item of "
            "its own, and link them with parents: or subtasks:"
        ),
        "parent": "Write parents: [T123], the full list or +T123",
    },
}

#: What marks a list entry as "add this" and "remove this".
ADD = "+"
REMOVE = "-"


class MixedListError(PhabfiveInputException):
    """A list that is partly a full set and partly a delta."""


@dataclasses.dataclass(frozen=True)
class ListEdit:
    """What one list-valued key asks for.

    Attributes
    ----------
    delta : bool
        False when the list is the full desired set, True when every entry
        adds or removes.
    values : tuple of str
        The full set, in the order written. Empty for a delta.
    added, removed : tuple of str
        The entries of a delta, without their operator. Empty for a full
        set.
    """

    delta: bool
    values: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @property
    def named(self) -> tuple[str, ...]:
        """Every value the list mentions, whatever it asks to do with it."""
        return self.values + self.added + self.removed


def list_entries(value: Any) -> list[Any]:
    """A list-valued key's entries, however the document wrote them.

    A list, or one comma-separated string - the two spellings every list key
    in a spec accepts.
    """
    if isinstance(value, (list, tuple)):
        return list(value)

    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]

    return [value]


def parse_list_edit(value: Any) -> ListEdit:
    """Read a list-valued key as a full set or as a delta.

    Parameters
    ----------
    value : list or str
        The key's value. An empty list is the full set "nothing".

    Raises
    ------
    MixedListError
        Some entries carry ``+`` or ``-`` and some do not. Guessing which
        was meant would either drop everything not listed or keep everything
        that was meant to go.
    PhabfiveInputException
        An entry is not text - ``+1`` without quotes is the number 1 in
        YAML - or is an operator with nothing after it.
    """
    entries = list_entries(value)

    for entry in entries:
        if isinstance(entry, str):
            continue

        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            # `+1` and `-1` without quotes are numbers in YAML, and the
            # operator went with them
            raise PhabfiveInputException(
                f'{entry!r} is a number, not text. Quote it: "{_sign(entry)}{entry}"'
            )

        raise PhabfiveInputException(f"{entry!r} is a {type(entry).__name__}, not text")

    marked = [entry for entry in entries if entry[:1] in (ADD, REMOVE)]

    if not marked:
        return ListEdit(delta=False, values=tuple(entries))

    if len(marked) != len(entries):
        plain = [entry for entry in entries if entry not in marked]
        raise MixedListError(
            f"{', '.join(plain)} carries no + or - while {', '.join(marked)} "
            f"does. A list is either the full set or only additions and "
            f"removals"
        )

    added: list[str] = []
    removed: list[str] = []

    for entry in marked:
        name = entry[1:].strip()

        if not name:
            raise PhabfiveInputException(f"{entry!r} names nothing to add or remove")

        (added if entry[0] == ADD else removed).append(name)

    return ListEdit(delta=True, added=tuple(added), removed=tuple(removed))


def _sign(number: Any) -> str:
    """The operator YAML swallowed when it read ``+1`` as a number."""
    return "" if number < 0 else ADD


def parse_stamp(value: Any) -> int:
    """A `modified:` value as seconds since the epoch.

    Text is ISO 8601, with ``Z`` or an offset; without either it is read as
    UTC. A native timestamp - what YAML and TOML make of an unquoted one -
    is the same instant, so a document says the same thing in every
    serialization.

    Raises
    ------
    PhabfiveInputException
        The value is not a timestamp.
    """
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()

        if text[-1:] in ("Z", "z"):
            text = f"{text[:-1]}+00:00"

        try:
            moment = datetime.datetime.fromisoformat(text)
        except ValueError:
            raise PhabfiveInputException(
                f"{value!r} is not an ISO 8601 timestamp such as 2026-01-31T09:30:00Z"
            ) from None

        if "T" not in text.upper() and " " not in text:
            raise PhabfiveInputException(
                f"{value!r} is a date with no time. Write it to the second, "
                f"such as {text}T09:30:00Z"
            )
    else:
        raise PhabfiveInputException(
            f"{value!r} is a {type(value).__name__}, not an ISO 8601 timestamp "
            f"such as 2026-01-31T09:30:00Z"
        )

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)

    return int(moment.timestamp())


def format_stamp(epoch: int) -> str:
    """Seconds since the epoch as the text `parse_stamp` reads back."""
    moment = datetime.datetime.fromtimestamp(int(epoch), tz=datetime.timezone.utc)

    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
