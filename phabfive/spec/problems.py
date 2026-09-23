# -*- coding: utf-8 -*-
"""The one problem record every spec validation layer reports through.

Standard library only, and no phabfive imports at all: a `Problem` is what
the offline pass, the online pass, `phabfive spec validate` and a web
frontend all hold, so it must be the cheapest thing in the subpackage to
import.

A `Problem` is *reported*, never raised. The dividing line for the whole
spec subpackage is: **structure raises, content is reported**. A file that
cannot be parsed and a server that could not be asked are not `Problem`s -
there is no spec to attach one to, and a clean report for a check that never
ran is worse than an error.

`code` is the stable, kebab-case slug a CI job or a frontend branches on;
`reason` is the sentence a human reads and nothing parses. `CODES` below is
the whole vocabulary, and it is the authoritative list: a check that emits a
slug missing from it fails `tests/test_spec_problems.py`, so the list cannot
quietly fall behind the checks the way a docstring does. Extend it in the
issue that adds the check, and never reuse a slug for a second meaning.

`field` shadows `dataclasses.field` and `object` shadows the builtin. Both
names are what the emitted JSON record has to be called, so they stay, and
this module writes `dataclasses.field(...)` rather than importing the name.
"""

import dataclasses
from typing import Any


#: Every `code` any layer emits, plus the ones reserved for the checks
#: Phases 2 and 3 add. Grouped by who reports them so a reader can see at a
#: glance what a layer can say.
CODES: tuple[str, ...] = (
    # The loader and the envelope
    "unknown-spec-version",
    # The offline layer: shape, keys and types
    "unknown-key",
    "deprecated-key",
    "wrong-type",
    "missing-required",
    "unknown-value",
    "bad-time",
    "bad-pattern",
    "bad-policy",
    "bad-monogram",
    # The offline layer: variables
    "undefined-variable",
    "missing-variable",
    "circular-variable",
    # The offline layer: local ids
    "duplicate-local-id",
    "unknown-local-id",
    "local-id-cycle",
    "bad-local-id",
    # The online layer
    "unknown-user",
    "ambiguous-user",
    "unknown-project",
    "ambiguous-project",
    "unknown-space",
    # A warning, never an error: the icon set is instance configuration that
    # no Conduit method reports, so an icon outside the observed set may be
    # one `projects.icons` configured and no project carries yet.
    "unknown-icon",
    "unknown-status",
    "unknown-priority",
    "unknown-column",
    "hashtag-taken",
    "not-creatable",
    "unknown-reference",
    # `phabfive spec validate` itself, for the two things that raise rather
    # than report: a file it could not read, an instance it could not ask
    "unreadable",
    "unreachable",
)


class Severity:
    """How much a problem costs. Only ERROR affects an exit status."""

    ERROR = "error"
    WARNING = "warning"


class Layer:
    """Which validation pass found a problem, set by the pass, not the check."""

    OFFLINE = "offline"
    ONLINE = "online"


@dataclasses.dataclass(frozen=True)
class Problem:
    """One thing wrong with one field of one object in a spec.

    Frozen and compared by value, so a test asserts a whole expected list at
    once rather than picking at attributes.

    Attributes
    ----------
    object : str
        Which item in the spec, as a path a human can find: "tasks[0]",
        "tasks[0].tasks[2]", "searches[1]", "variables", "metadata", or "$"
        for the document itself. Never a monogram - nothing has one yet.
    field : str or None
        The key inside that object: "priority", "projects[0]",
        "search.column". None when the problem is the object itself, such as
        a task with no title.
    value : object
        Exactly what was there - the string, number, list or None. None also
        means "the key was missing"; `field` and `reason` disambiguate. Must
        survive json.dumps(default=str).
    reason : str
        One sentence naming the offending value. Rendered, never parsed.
    code : str
        A stable kebab-case slug; see the module docstring.
    layer : str
        Layer.OFFLINE or Layer.ONLINE.
    severity : str
        Severity.ERROR or Severity.WARNING.
    """

    object: str
    field: str | None
    value: Any
    reason: str
    code: str
    layer: str = Layer.OFFLINE
    severity: str = Severity.ERROR

    def as_record(self) -> dict[str, Any]:
        """The {object, field, value, reason, code, layer, severity} dict.

        This is exactly what phabfive.json_output emits, so a frontend and a
        `--format=json` reader see the same record.
        """
        return {
            "object": self.object,
            "field": self.field,
            "value": self.value,
            "reason": self.reason,
            "code": self.code,
            "layer": self.layer,
            "severity": self.severity,
        }


def problem(
    object: str,
    *,
    reason: str,
    code: str,
    field: str | None = None,
    value: Any = None,
    layer: str = Layer.OFFLINE,
    severity: str = Severity.ERROR,
) -> Problem:
    """Build a Problem without repeating the defaults at every call site.

    The positional argument is the object path; everything else is a keyword,
    because `Problem("tasks[0]", None, None, "...", "...")` is unreadable and
    a check that silently swaps `reason` and `code` is worse.
    """
    return Problem(
        object=object,
        field=field,
        value=value,
        reason=reason,
        code=code,
        layer=layer,
        severity=severity,
    )


__all__ = [
    "CODES",
    "Layer",
    "Problem",
    "Severity",
    "problem",
]
