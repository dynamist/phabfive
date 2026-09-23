# -*- coding: utf-8 -*-
"""Everything about a spec that can be decided without touching the server.

This is layer 1 of two::

    parse -> validate offline -> validate online -> plan -> apply
             THIS MODULE        needs a token      only exists if both passed
             no network
             no token
             no configuration

`validate_offline` **returns** its report rather than printing or raising it:
a list of `phabfive.spec.problems.Problem` records, which is what a web
frontend renders as per-field form errors and what the CLI turns into an
exit status. It returns ``[]`` for a clean spec, and it never raises on a
validation failure - that is the whole point. The dividing line for the spec
subpackage is **structure raises, content is reported**: a file that cannot
be parsed has no `Spec` to hang a problem on and raises out of the loader,
while everything reachable from a `Spec` comes back as a record.

**Every check runs.** A spec with six mistakes reports six problems. A
first-error-wins implementation would make the report useless for a form and
useless for a CI job, so nothing here short-circuits.

**It constructs nothing.** No `Phabfive`, no `get_app`, no `~/.arcrc`, no
`.arcconfig`, no `~/.config/phabfive.yaml`, no environment, no socket. A
repository of specs has to be checkable in CI or a pre-commit hook on a
machine that has never seen a token, and `tests/test_spec_isolation.py`
proves it out of process rather than leaving it as an intention.

What is deliberately **not** decided here: whether a priority, a status, a
project icon, a space or a policy *name* exists. Those are defined by the
instance, and phabfive answers a failed status fetch with invented defaults
- so deciding them offline would mean blessing a value the server never
named. They are typed and shape-checked here and resolved by
`phabfive.spec.online`.

A project **colour** is decided here, and is the one that looks like it
should not be: the colour keys are fixed in Phorge's own source, where
`projects.colors` relabels a colour but cannot add one, so a colour needs no
token and no network. It is a `FieldKind.ENUM` with its choices on the field.
"""

from __future__ import annotations

import dataclasses
import difflib
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Optional

from phabfive.exceptions import PhabfiveException
from phabfive.spec.envelope import (
    METADATA_KEYS,
    SUPPORTED_SPEC_VERSIONS,
    Kind,
)
from phabfive.spec.problems import Problem, Severity, problem
from phabfive.spec.references import (
    LOCAL_ID_KEY,
    REFERENCE_FIELDS,
    RefKind,
    ReferenceField,
    is_local_id,
    is_monogram,
    iter_objects,
    iter_references,
    local_id,
)
from phabfive.spec.registry import (
    DECLARED_COMPLETE,
    Field,
    FieldKind,
    field_by_name,
    fields_for,
)
from phabfive.spec.schema import SEARCH_ITEM_KEYS
from phabfive.spec.times import parse_time_with_unit
from phabfive.spec.variables import (
    build_dependency_graph,
    declared_value,
    detect_circular_dependencies,
    extract_variable_dependencies,
    render_tree,
    resolve_variables,
)

if TYPE_CHECKING:  # pragma: no cover - annotations only, never imported
    from phabfive.spec.envelope import Spec

__all__ = [
    "DOCUMENT",
    "validate_offline",
]

#: The object path of a problem with the document itself rather than an item.
DOCUMENT = "$"

# A string still carrying Jinja syntax was not rendered, because rendering it
# would have needed a variable nothing supplies. Checking its *value* would
# report "{{ prio }} is not a priority" on top of "prio is undefined", which
# is two problems for one mistake.
_UNRENDERED = "{{"

# How close a misspelling has to be before a report offers the real key.
_SUGGESTION_CUTOFF = 0.6


def _unrendered(value: object) -> bool:
    """Whether a value is a string that still holds a variable reference."""
    return isinstance(value, str) and _UNRENDERED in value


def _suggestion(key: str, known: Sequence[str]) -> str:
    """ " Did you mean ..." when one declared key is close enough, else ""."""
    matches = difflib.get_close_matches(key, known, n=1, cutoff=_SUGGESTION_CUTOFF)

    return f" Did you mean {matches[0]!r}?" if matches else ""


def _type_name(value: object) -> str:
    """What to call a value's type in a sentence a person reads."""
    if value is None:
        return "nothing"

    return type(value).__name__


# --------------------------------------------------------------------------
# 1. The envelope
# --------------------------------------------------------------------------


def _check_envelope(spec: "Spec") -> list[Problem]:
    """`spec:`, `kind:` and `metadata:`.

    The loader refuses an unknown `spec:` version and a `kind:` that is not a
    kind, so a spec that came through `load_spec` never fails the first two.
    A spec a program built by hand - a web frontend assembling an `Envelope`
    itself - can, and is what these two guard.
    """
    problems: list[Problem] = []
    envelope = spec.envelope

    if envelope.spec is not None and envelope.spec not in SUPPORTED_SPEC_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_SPEC_VERSIONS))
        problems.append(
            problem(
                DOCUMENT,
                field="spec",
                value=envelope.spec,
                reason=(
                    f"This phabfive does not read spec version "
                    f"{envelope.spec!r}. It reads: {supported}"
                ),
                code="unknown-spec-version",
            )
        )

    if not isinstance(envelope.kind, Kind):
        problems.append(
            problem(
                DOCUMENT,
                field="kind",
                value=envelope.kind,
                reason=(
                    f"A spec is one of: {', '.join(member.value for member in Kind)}"
                ),
                code="wrong-type",
            )
        )

    problems += _check_metadata(spec)

    return problems


def _check_metadata(spec: "Spec") -> list[Problem]:
    """What a file says about itself.

    A `version: 2.1` written without quotes is a YAML float and a real trap -
    the file means "version 2.1" and gets `2.1`, which stops being the same
    string the moment anybody compares it. It is reported rather than coerced,
    because coercing it hides the mistake from whoever has to fix the file.
    """
    problems: list[Problem] = []
    metadata = spec.metadata

    for key in METADATA_KEYS:
        value = getattr(metadata, key)

        if value is not None and not isinstance(value, str):
            problems.append(
                problem(
                    "metadata",
                    field=key,
                    value=value,
                    reason=(
                        f"metadata.{key} is text, not a {_type_name(value)}. "
                        f'Quote it: {key}: "{value}"'
                    ),
                    code="wrong-type",
                )
            )

    for key in metadata.extra:
        problems.append(
            problem(
                "metadata",
                field=key,
                value=metadata.extra[key],
                reason=(
                    f"metadata has no key {key!r}, so nothing reads it."
                    f"{_suggestion(key, METADATA_KEYS)}"
                ),
                code="unknown-key",
                severity=Severity.WARNING,
            )
        )

    return problems


# --------------------------------------------------------------------------
# 2. The variables, and 3. the render they allow
# --------------------------------------------------------------------------


def _strings(
    value: Any, *, object_path: str, field_path: str
) -> Iterator[tuple[str, str, str]]:
    """Every string in a nested structure, with the path that names it.

    Mapping *keys* are spec keys rather than user text and are not yielded;
    the schema pass is what has an opinion about a key.
    """
    if isinstance(value, str):
        yield object_path, field_path, value
        return

    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{field_path}.{key}" if field_path else str(key)
            yield from _strings(item, object_path=object_path, field_path=child)
        return

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, item in enumerate(value):
            yield from _strings(
                item, object_path=object_path, field_path=f"{field_path}[{index}]"
            )


def _body_strings(spec: "Spec") -> Iterator[tuple[str, str, str]]:
    """Every string in a spec's body, named by object and field."""
    for spec_object in iter_objects(spec):
        for key, value in spec_object.data.items():
            # A nested task is an object of its own and is walked as one, so
            # walking into it here would report each of its strings twice
            if spec_object.object_type == "task" and key == "tasks":
                continue

            yield from _strings(
                value, object_path=spec_object.path, field_path=str(key)
            )

    # Body keys outside the item sections - a create spec's stray top-level
    # key, say - still render, so a variable they name still has to exist
    handled = {"tasks", "projects", "pastes", "searches"}
    for key, value in spec.body.items():
        if key in handled:
            continue

        yield from _strings(value, object_path=DOCUMENT, field_path=str(key))


def _check_variables(
    spec: "Spec", overrides: Mapping[str, Any]
) -> tuple[list[Problem], dict[str, Any]]:
    """Which names the spec reads, and whether every one of them has a value.

    Returns the problems and the effective declared values, so that step 3
    can render with them when there is nothing wrong.
    """
    problems: list[Problem] = []
    declared = dict(spec.variables)

    effective: dict[str, Any] = {}
    for name, declaration in declared.items():
        if name in overrides:
            effective[name] = overrides[name]
            continue

        supplied, value = declared_value(declaration)

        if not supplied:
            problems.append(
                problem(
                    "variables",
                    field=name,
                    value=declaration,
                    reason=(
                        f"Variable {name!r} has no value: give it one under "
                        f"variables:, write {{default: <value>}}, or supply it "
                        f"with --set"
                    ),
                    code="missing-variable",
                )
            )
            continue

        effective[name] = value

    for name, value in overrides.items():
        effective.setdefault(name, value)

    graph = build_dependency_graph(effective)
    has_cycle, cycle = detect_circular_dependencies(graph)

    if has_cycle:
        problems.append(
            problem(
                "variables",
                field=cycle[0] if cycle else None,
                value=" → ".join(cycle),
                reason=(
                    f"The variables refer to one another in a circle: "
                    f"{' → '.join(cycle)}"
                ),
                code="circular-variable",
            )
        )

    known = set(effective)

    # Every name the variables themselves read, then every name the body
    # reads. Reported once per place that reads it, because that is the place
    # somebody has to go and fix
    for name, value in declared.items():
        if isinstance(value, str):
            problems += _undefined(
                "variables", name, value, known, declared_names=set(declared)
            )

    for object_path, field_path, value in _body_strings(spec):
        problems += _undefined(
            object_path, field_path, value, known, declared_names=set(declared)
        )

    return problems, effective


def _undefined(
    object_path: str,
    field_path: str,
    value: str,
    known: set[str],
    *,
    declared_names: set[str],
) -> list[Problem]:
    """An `undefined-variable` problem per name in one string nothing supplies."""
    problems: list[Problem] = []

    for name in sorted(extract_variable_dependencies(value)):
        if name in known:
            continue

        problems.append(
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=(
                    f"{{{{ {name} }}}} is not declared under variables: and was "
                    f"not supplied.{_suggestion(name, sorted(declared_names))}"
                ),
                code="undefined-variable",
            )
        )

    return problems


def _rendered(spec: "Spec", effective: Mapping[str, Any]) -> Optional["Spec"]:
    """A copy of the spec with its variables rendered in, or None.

    None means "could not render", which is not a new problem: whatever
    stopped it was already reported by `_check_variables`, and the later
    passes carry on with the unrendered body while skipping any value check
    on a string that still holds `{{`.
    """
    try:
        resolved = resolve_variables(dict(spec.variables), dict(effective))
        body = render_tree(dict(spec.body), resolved)
    except PhabfiveException:
        return None

    return dataclasses.replace(spec, body=body, rendered=True)


# --------------------------------------------------------------------------
# 4. The schema
# --------------------------------------------------------------------------


def _check_document_keys(spec: "Spec") -> list[Problem]:
    """The body's own top-level keys."""
    from phabfive.spec.references import CREATE_OBJECT_KEYS

    known: tuple[str, ...] = (
        ("searches",)
        if spec.kind is Kind.SEARCH
        else tuple(key for key, _ in CREATE_OBJECT_KEYS)
    )

    problems: list[Problem] = []

    for key in spec.body:
        if key in known:
            continue

        problems.append(
            problem(
                DOCUMENT,
                field=str(key),
                value=spec.body[key],
                reason=(
                    f"A {spec.kind.value} spec has no top-level key {key!r}. "
                    f"It holds: {', '.join(known)}."
                    f"{_suggestion(str(key), known)}"
                ),
                code="unknown-key",
            )
        )

    return problems


def _check_shape(spec: "Spec") -> list[Problem]:
    """Every item section holds a list of mappings.

    `iter_objects` skips anything that is not a mapping, so this is what
    reports it - once, under the path it would have had.
    """
    from phabfive.spec.references import CREATE_OBJECT_KEYS

    sections: tuple[tuple[str, str], ...] = (
        (("searches", "search"),) if spec.kind is Kind.SEARCH else CREATE_OBJECT_KEYS
    )

    problems: list[Problem] = []

    for key, _ in sections:
        for index, item in enumerate(spec.items(key)):
            if not isinstance(item, Mapping):
                problems.append(
                    problem(
                        f"{key}[{index}]",
                        field=None,
                        value=item,
                        reason=(
                            f"A {key}: item is a mapping of keys, not a "
                            f"{_type_name(item)}"
                        ),
                        code="wrong-type",
                    )
                )

    return problems


# The value of a key whose spec text was one Jinja expression is whatever
# Jinja returned, and Jinja returns text: `limit: "{{ n }}"` with n = 5
# renders to "5", not to 5. Reporting that as "limit takes a whole number"
# would refuse a spec that is perfectly well written, and there is no way to
# write an unquoted `{{ ... }}` in YAML, so a templated value of a kind whose
# type rendering changes is left to the apply that coerces it. Every other
# kind is text before and after, so `priority: "{{ prio }}"` is checked as
# the priority it became.
_RETYPED_BY_RENDERING = frozenset({FieldKind.INT, FieldKind.BOOL})


def _check_value(
    object_path: str,
    field_path: str,
    field: Field,
    value: Any,
    templated: frozenset[tuple[str, str]] = frozenset(),
) -> list[Problem]:
    """One value against one declared field.

    This is where the registry's `FieldKind` decides what a value may be, and
    where it decides to say nothing: a priority, a status, an icon, a space
    or a policy *name* is the instance's to define, so those come back
    checked for type and shape and no further. A project colour is not among
    them - Phorge fixes those keys in its source, so the `ENUM` branch below
    settles one from `field.choices`.

    `templated` is the set of ``(object, field)`` pairs whose spec text held
    ``{{``, taken from the spec before it was rendered.
    """
    if _unrendered(value):
        return []

    kind = field.kind

    if kind in _RETYPED_BY_RENDERING and (object_path, field_path) in templated:
        return []

    def wrong(expected: str) -> list[Problem]:
        return [
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=f"{field.name} takes {expected}, not a {_type_name(value)}",
                code="wrong-type",
            )
        ]

    if kind is FieldKind.BOOL:
        return [] if isinstance(value, bool) else wrong("true or false")

    if kind is FieldKind.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            return wrong("a whole number")
        return []

    if kind is FieldKind.TIME:
        return _check_time(object_path, field_path, field, value)

    if kind is FieldKind.PATTERN:
        return _check_pattern(object_path, field_path, field, value)

    if kind is FieldKind.POLICY:
        return _check_policy(object_path, field_path, field, value)

    if kind is FieldKind.MONOGRAM:
        return _check_monograms(object_path, field_path, field, value)

    if kind is FieldKind.ENUM:
        # A list-valued enum is each entry against the choices, not the list
        # against them: `colors: [red, blue]` is two colours, and so is
        # `colors: red,blue`, which is the spelling every list filter in
        # phabfive also accepts.
        entries = _entries(field, value)

        return [
            problem(
                object_path,
                field=field_path,
                value=entry,
                reason=(
                    f"{field.name} is one of: {', '.join(field.choices)} "
                    f"(got {entry!r})"
                ),
                code="unknown-value",
            )
            for entry in entries
            if not _unrendered(entry) and entry not in field.choices
        ]

    if kind is FieldKind.ORDER:
        return _check_order(object_path, field_path, field, value)

    # TEXT, USER, PROJECT, SPACE and INSTANCE_ENUM are text offline
    if not isinstance(value, str):
        if field.multiple and isinstance(value, (list, tuple)):
            return _check_each_string(object_path, field_path, field, value)
        return wrong("text")

    return []


def _entries(field: Field, value: Any) -> list:
    """One field's values, however the spec wrote them.

    A `multiple` field accepts a list and a comma-separated string alike -
    the rule `phabfive.options.value_list` states for the commands - so a
    check over its values has to read both. A field that is not `multiple`
    has exactly one value, whatever its shape.
    """
    if not field.multiple:
        return [value]

    if isinstance(value, (list, tuple)):
        return list(value)

    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]

    return [value]


def _check_each_string(
    object_path: str, field_path: str, field: Field, values: Sequence[Any]
) -> list[Problem]:
    """Each item of a list-valued text field is text."""
    problems: list[Problem] = []

    for index, item in enumerate(values):
        if _unrendered(item) or isinstance(item, str):
            continue

        problems.append(
            problem(
                object_path,
                field=f"{field_path}[{index}]",
                value=item,
                reason=f"{field.name} takes text, not a {_type_name(item)}",
                code="wrong-type",
            )
        )

    return problems


def _check_time(
    object_path: str, field_path: str, field: Field, value: Any
) -> list[Problem]:
    """`1h`, `7d`, `2w`, or a bare number of days.

    Parsed by `phabfive.spec.times.parse_time_with_unit`, which is the same
    function `maniphest search --created-after` runs - it lives in
    `phabfive/spec/` precisely so that this pass can call it without
    importing `phabfive.maniphest`, and with it `phabfive.core` and the
    `phabricator` client, onto the one code path whose whole claim is that it
    needs neither.

    Parsed rather than matched against `phabfive.spec.schema.TIME_PATTERN`,
    because that published pattern is the documented grammar and is
    deliberately narrower than what the parser's backward-compatible "a bare
    number is days" branch hands to `float()`. Reporting `bad-time` for a
    value the search would have run is a false positive; refusing to publish
    `nan` as a legal time is not.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return [
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=(
                    f"{field.name} takes a time such as 1h, 7d or 2w, not a "
                    f"{_type_name(value)}"
                ),
                code="wrong-type",
            )
        ]

    try:
        parse_time_with_unit(value)
    except PhabfiveException as error:
        return [
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=str(error),
                code="bad-time",
            )
        ]

    return []


def _check_pattern(
    object_path: str, field_path: str, field: Field, value: Any
) -> list[Problem]:
    """A transition filter, parsed by the same parser the search runs.

    Parsed rather than matched against the generated regular expression, so
    the sentence a person reads is the one the command would have given them.
    The pattern names a column, a status or a priority, and *which* ones an
    instance has is the online pass's question.
    """
    from phabfive import transitions

    parsers = {
        "column": transitions.parse_column_patterns,
        "status": transitions.parse_status_patterns,
        "priority": transitions.parse_priority_patterns,
    }
    parse = parsers.get(field.name)

    if not isinstance(value, str):
        return [
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=(
                    f"{field.name} takes a transition filter such as "
                    f"in:Backlog, not a {_type_name(value)}"
                ),
                code="wrong-type",
            )
        ]

    if parse is None:
        return []

    try:
        parse(value)
    except PhabfiveException as error:
        return [
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=str(error),
                code="bad-pattern",
            )
        ]

    return []


def _check_policy(
    object_path: str, field_path: str, field: Field, value: Any
) -> list[Problem]:
    """A policy's shape: a keyword, a PHID, a #project or an @user.

    Shape only. Whether that project or user exists needs the server, and
    Conduit reads an unrecognised policy as one nobody satisfies - so a typo
    comes back as a permissions error unless it is caught here first.
    """
    from phabfive.policy import validate_policy_value

    if not isinstance(value, str):
        return [
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=f"{field.name} takes a policy, not a {_type_name(value)}",
                code="wrong-type",
            )
        ]

    try:
        validate_policy_value(value, option=field.name)
    except PhabfiveException as error:
        return [
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=str(error),
                code="bad-policy",
            )
        ]

    return []


def _check_monograms(
    object_path: str, field_path: str, field: Field, value: Any
) -> list[Problem]:
    """`T123`, or a comma-separated string of them, or a list of them.

    Restricted to the applications `field.monograms` names, so `include:
    P45` is refused here rather than by `maniphest search` at the other end
    - a paste is a well-formed monogram and a well-formed nothing for a key
    that takes task ids.
    """
    expected = ", ".join(f"{letter}123" for letter in field.monograms) or "T123"
    if isinstance(value, (list, tuple)):
        pairs = [(f"{field_path}[{index}]", one) for index, one in enumerate(value)]
    else:
        pairs = [(field_path, value)]

    problems: list[Problem] = []

    for path, one in pairs:
        if _unrendered(one):
            continue

        if not isinstance(one, str):
            problems.append(
                problem(
                    object_path,
                    field=path,
                    value=one,
                    reason=(
                        f"{field.name} takes task ids such as {expected}, "
                        f"not a {_type_name(one)}"
                    ),
                    code="wrong-type",
                )
            )
            continue

        for part in one.split(","):
            part = part.strip()

            if not part or is_monogram(part, prefixes=field.monograms):
                continue

            problems.append(
                problem(
                    object_path,
                    field=path,
                    value=one,
                    reason=(f"{part!r} is not a task id. Expected format: {expected}"),
                    code="bad-monogram",
                )
            )

    return problems


def _check_order(
    object_path: str, field_path: str, field: Field, value: Any
) -> list[Problem]:
    """`<field>[:asc|:desc]`, from the one list of order spellings."""
    from phabfive.constants import MANIPHEST_ORDER_CHOICES

    if not isinstance(value, str):
        return [
            problem(
                object_path,
                field=field_path,
                value=value,
                reason=f"{field.name} takes text, not a {_type_name(value)}",
                code="wrong-type",
            )
        ]

    if value in MANIPHEST_ORDER_CHOICES:
        return []

    return [
        problem(
            object_path,
            field=field_path,
            value=value,
            reason=(
                f"{field.name} is one of: {', '.join(MANIPHEST_ORDER_CHOICES)} "
                f"(got {value!r})"
            ),
            code="unknown-value",
        )
    ]


def _check_fields(
    object_path: str,
    field_prefix: str,
    mapping: Mapping[str, Any],
    object_type: str,
    verb: str,
    templated: frozenset[tuple[str, str]] = frozenset(),
) -> list[Problem]:
    """One mapping against the fields the registry declares for it.

    Two questions, answered separately, because the registry answers them
    separately:

    - **Is this key known?** Only for a pair in
      `registry.DECLARED_COMPLETE`, whose key set is finished. A registry
      that has declared two keys of an object cannot honestly call the third
      unknown, and declaring the first field of an object type would
      otherwise turn every other key of it into an error overnight.
    - **Is this value right?** For every declared key, wherever it is. A
      colour is checked in a `projects:` item today even though the rest of
      that item's keys are not yet declared.
    """
    declared = fields_for(object_type, verb)

    if not declared:
        return []

    problems: list[Problem] = []
    known = [field.name for field in declared]
    complete = (object_type, verb) in DECLARED_COMPLETE

    if complete:
        for raw_key in mapping:
            key = str(raw_key)

            if field_by_name(key, object_type, verb) is not None:
                continue

            problems.append(
                problem(
                    object_path,
                    field=f"{field_prefix}{key}",
                    value=mapping[raw_key],
                    reason=(
                        f"A {object_type} {verb} has no key {key!r}."
                        f"{_suggestion(key, known)}"
                    ),
                    code="unknown-key",
                )
            )

    # Declaration order, so the report is deterministic and therefore testable
    for field in declared:
        if field.name not in mapping:
            continue

        path = f"{field_prefix}{field.name}"
        value = mapping[field.name]

        if field.deprecated:
            problems.append(
                problem(
                    object_path,
                    field=path,
                    value=value,
                    reason=(
                        f"{field.name} still works but is deprecated: write "
                        f"{field.deprecated} instead"
                    ),
                    code="deprecated-key",
                    severity=Severity.WARNING,
                )
            )

        problems += _check_value(object_path, path, field, value, templated)

    return problems


def _check_schema(
    spec: "Spec", templated: frozenset[tuple[str, str]] = frozenset()
) -> list[Problem]:
    """Known keys, value types and the genuinely static enums."""
    problems = _check_document_keys(spec)
    problems += _check_shape(spec)

    for spec_object in iter_objects(spec):
        if spec_object.object_type == "search":
            problems += _check_search_item(
                spec_object.path, spec_object.data, templated
            )
            continue

        problems += _check_fields(
            spec_object.path,
            "",
            spec_object.data,
            spec_object.object_type,
            "create",
            templated,
        )

    return problems


def _check_search_item(
    object_path: str,
    item: Mapping[str, Any],
    templated: frozenset[tuple[str, str]] = frozenset(),
) -> list[Problem]:
    """One `searches:` item: what it searches, its banner, and its filters."""
    from phabfive.spec.registry import OBJECT_TYPES

    problems: list[Problem] = []

    for key in item:
        if str(key) not in SEARCH_ITEM_KEYS:
            problems.append(
                problem(
                    object_path,
                    field=str(key),
                    value=item[key],
                    reason=(
                        f"A searches: item has no key {str(key)!r}. It holds: "
                        f"{', '.join(SEARCH_ITEM_KEYS)}."
                        f"{_suggestion(str(key), SEARCH_ITEM_KEYS)}"
                    ),
                    code="unknown-key",
                )
            )

    object_type = item.get("type")

    if not isinstance(object_type, str) or object_type not in OBJECT_TYPES:
        problems.append(
            problem(
                object_path,
                field="type",
                value=object_type,
                reason=(
                    f"type is what this item searches, one of: "
                    f"{', '.join(sorted(OBJECT_TYPES))} (got {object_type!r})"
                ),
                code="unknown-value",
            )
        )
        object_type = None

    for key in ("title", "description"):
        value = item.get(key)

        if value is not None and not isinstance(value, str):
            problems.append(
                problem(
                    object_path,
                    field=key,
                    value=value,
                    reason=(
                        f"{key} is this search's banner text, not a {_type_name(value)}"
                    ),
                    code="wrong-type",
                )
            )

    search = item.get("search")

    if search is None:
        return problems

    if not isinstance(search, Mapping):
        problems.append(
            problem(
                object_path,
                field="search",
                value=search,
                reason=(f"search: is a mapping of filters, not a {_type_name(search)}"),
                code="wrong-type",
            )
        )
        return problems

    if object_type is not None:
        problems += _check_fields(
            object_path, "search.", search, object_type, "search", templated
        )

    return problems


# --------------------------------------------------------------------------
# 5. The static semantics a JSON Schema cannot express
# --------------------------------------------------------------------------


def _check_local_ids(spec: "Spec") -> tuple[list[Problem], dict[str, str]]:
    """`id:` declarations: well formed, and each one used once.

    Returns the problems and the local ids that are usable, so that the
    reference check reports "no object is called that" only for a name
    nothing declares rather than for one declared twice.
    """
    problems: list[Problem] = []
    seen: dict[str, str] = {}

    for spec_object in iter_objects(spec):
        if LOCAL_ID_KEY not in spec_object.data:
            continue

        value = spec_object.data[LOCAL_ID_KEY]

        if not is_local_id(value):
            problems.append(
                problem(
                    spec_object.path,
                    field=LOCAL_ID_KEY,
                    value=value,
                    reason=(
                        f"id: names this object inside the spec and is written "
                        f"as a letter or digit followed by letters, digits, "
                        f"'-', '_' or '.' (got {value!r})"
                    ),
                    code="bad-local-id",
                )
            )
            continue

        if value in seen:
            problems.append(
                problem(
                    spec_object.path,
                    field=LOCAL_ID_KEY,
                    value=value,
                    reason=(
                        f"Two objects are called ${value}: this one and "
                        f"{seen[value]}. A local id names one object"
                    ),
                    code="duplicate-local-id",
                )
            )
            continue

        seen[value] = spec_object.path

    return problems, seen


def _declared_field(object_type: str, name: str) -> Optional[ReferenceField]:
    """The reference field one key of one object type is, if it is one."""
    for declared in REFERENCE_FIELDS.get(object_type, ()):
        if declared.name == name:
            return declared

    return None


def _check_references(spec: "Spec", local_ids: Mapping[str, str]) -> list[Problem]:
    """Every `$name` names an object this spec declares, and monograms are monograms."""
    problems: list[Problem] = []

    for reference in iter_references(spec):
        if _unrendered(reference.value):
            continue

        name = reference.field.split("[")[0]
        declared = _declared_field(reference.object_type, name)

        if reference.kind is RefKind.LOCAL:
            target = local_id(reference.value)

            if target is not None and target not in local_ids:
                known = sorted(local_ids)
                problems.append(
                    problem(
                        reference.object,
                        field=reference.field,
                        value=reference.value,
                        reason=(
                            f"No object in this spec is called ${target}."
                            f"{_suggestion(target, known)}"
                            if known
                            else (
                                f"No object in this spec is called ${target}, "
                                f"and this spec declares no id: at all"
                            )
                        ),
                        code="unknown-local-id",
                    )
                )
            continue

        if isinstance(reference.value, str) and reference.value.startswith("$"):
            # `$` is the local-reference sigil and `classify_reference` did
            # not read this as one, so it is a local reference spelled wrong
            # rather than a project genuinely called "$ sprint".
            # `phabfive.spec.references` says the caller reports it; this is
            # that caller.
            problems.append(
                problem(
                    reference.object,
                    field=reference.field,
                    value=reference.value,
                    reason=(
                        f"{reference.value!r} starts with $, which names an "
                        f"object declared in this spec, but nothing usable "
                        f"follows it. A local id is a letter or digit "
                        f"followed by letters, digits, '-', '_' or '.'"
                    ),
                    code="bad-local-id",
                )
            )
            continue

        # A glob means something in a *filter* key and nothing in a key
        # that names what to attach: `maniphest create` refuses a "*" in
        # `projects:` with no network at all, so a spec writing one is
        # refused here rather than reported clean and then rejected by the
        # apply path - which is the one thing the two layers exist to keep
        # from happening.
        if (
            declared is not None
            and not declared.patterns
            and isinstance(reference.value, str)
            and "*" in reference.value
        ):
            problems.append(
                problem(
                    reference.object,
                    field=reference.field,
                    value=reference.value,
                    reason=(
                        f"{name} names what to attach, not a filter, so a "
                        f"wildcard is not a value it can use (got "
                        f"{reference.value!r})"
                    ),
                    code="unknown-value",
                )
            )
            continue

        # A key that names tasks takes a task id or a $local-id, and nothing
        # else: "Fix the thing" in parents: is a typo, not a task title
        if declared is not None and declared.monograms:
            if not is_monogram(reference.value, prefixes=declared.monograms):
                expected = ", ".join(f"{prefix}123" for prefix in declared.monograms)
                problems.append(
                    problem(
                        reference.object,
                        field=reference.field,
                        value=reference.value,
                        reason=(
                            f"{name} takes an existing task ({expected}) or a "
                            f"$local-id of an object this spec creates (got "
                            f"{reference.value!r})"
                        ),
                        code="bad-monogram",
                    )
                )

    return problems


def _check_local_id_cycles(spec: "Spec", local_ids: Mapping[str, str]) -> list[Problem]:
    """No object in the spec depends, however far round, on itself.

    Only `$local-id` references build the graph. Nesting cannot make a cycle -
    a task cannot be its own ancestor in a tree - so a cycle here is always
    two objects naming each other.
    """
    if not local_ids:
        return []

    path_to_id = {path: name for name, path in local_ids.items()}
    graph: dict[str, set[str]] = {name: set() for name in local_ids}

    for reference in iter_references(spec):
        if reference.kind is not RefKind.LOCAL:
            continue

        source = path_to_id.get(reference.object)
        target = local_id(reference.value)

        if source is None or target is None or target not in graph:
            continue

        graph[source].add(target)

    has_cycle, cycle = detect_circular_dependencies(graph)

    if not has_cycle:
        return []

    return [
        problem(
            local_ids[cycle[0]],
            field=LOCAL_ID_KEY,
            value=" → ".join(f"${name}" for name in cycle),
            reason=(
                "These objects refer to one another in a circle, so none of "
                "them can be created first: " + " → ".join(f"${name}" for name in cycle)
            ),
            code="local-id-cycle",
        )
    ]


def _check_semantics(spec: "Spec") -> list[Problem]:
    """What a JSON Schema structurally cannot say about a spec."""
    problems, local_ids = _check_local_ids(spec)
    problems += _check_references(spec, local_ids)
    problems += _check_local_id_cycles(spec, local_ids)

    return problems


# --------------------------------------------------------------------------
# The pass itself
# --------------------------------------------------------------------------


def validate_offline(
    spec: "Spec", *, variables: Optional[Mapping[str, Any]] = None
) -> list[Problem]:
    """Everything wrong with a spec that can be found without the server.

    Parameters
    ----------
    spec : Spec
        What `phabfive.spec.load_spec` or `Spec.from_data` returned.
    variables : mapping, optional
        Values supplied from outside the spec, which is what ``--set`` is on
        the command line. An override beats a declared default, and a name
        supplied here counts as declared for the undefined-variable check.

    Returns
    -------
    list of Problem
        Empty for a clean spec. **Every** problem, not the first: a spec with
        six mistakes comes back with six records, in document order - the
        envelope, then the variables, then each object in the order it
        appears with its fields in the order the registry declares them.

    Notes
    -----
    This never raises for a bad spec. It constructs no app, reads no
    configuration and opens no socket, so it runs in CI or a pre-commit hook
    on a machine with no token at all.

    The variables are rendered before the value checks, so
    ``priority: "{{ prio }}"`` is checked as the priority it becomes. When
    something stopped the render - an undefined name, a circle - the checks
    carry on against the unrendered body and skip any value still holding
    ``{{``, so one mistake is reported once.
    """
    overrides = dict(variables or {})

    problems = _check_envelope(spec)

    variable_problems, effective = _check_variables(spec, overrides)
    problems += variable_problems

    templated = frozenset(
        (object_path, field_path)
        for object_path, field_path, value in _body_strings(spec)
        if _UNRENDERED in value
    )

    checked = spec if variable_problems else _rendered(spec, effective)

    if checked is None:
        checked = spec

    problems += _check_schema(checked, templated)
    problems += _check_semantics(checked)

    return problems
