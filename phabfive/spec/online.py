# -*- coding: utf-8 -*-
"""Layer 2: everything about a spec that only the instance can answer.

The rule this module exists for is that **every** unresolvable reference is
reported in one report. A spec naming four users that do not exist reports
four problems from one call, not the first one four times over four runs.
That is the contract `phabfive/edit/plan.py` already states for a batch edit
- "every task is fetched and checked before any edit is built, and if one
fails, the error names every task that did" - generalised from tasks to
every kind of reference a spec can hold.

How it gets there:

1. :func:`index_references` walks the spec once, through
   `phabfive.spec.references.iter_references` - one grammar for both layers,
   not a second copy of it here - and builds a :class:`ReferenceIndex`:
   every reference in document order, plus the *distinct* values per
   :class:`~phabfive.spec.registry.FieldKind`. Each distinct value appears
   once however many items name it, which is what `create_tasks_from_config`
   already does by hand for users and spaces.
2. Each registered :class:`Resolver` is called **once**, with every value of
   its kind. One `user.search` for every username in the spec beats one per
   item, and one `maniphest.search` over every parent id beats today's one
   request per id.
3. Every failure becomes one :class:`~phabfive.spec.problems.Problem` per
   `(object, field)` that named the failing value, in document order.

What this module deliberately does not do:

- **It does not raise on a validation failure.** It returns a list, so a
  frontend can render the report as per-field form errors.
- **It converts no remote error into a problem.** `PhabfiveAPIException` and
  `PhabfiveConnectionException` propagate out of :func:`validate_online`
  untouched. Answering "this user does not exist" because the network was
  down is the exact failure this issue forbids, and returning an empty
  report would be worse: a clean verdict for a check that never ran.
- **It constructs nothing.** The app is handed in already built, so a web
  frontend passes a per-request `Maniphest(url=..., token=...)` and this
  module never reads `~/.arcrc`, a config file or the environment.
- **It writes nothing.** Every call it makes is a `*.search` or `whoami`.

Phase 1 shipped the protocol, the index, the atomic collection and one
resolver - :class:`ManiphestUserResolver`. Phase 2 adds
:class:`ProjectResolver`, :class:`SpaceResolver` and :class:`IconResolver`,
so a spec naming a user, a project, a Space and an icon that do not exist
reports all four from one run. The rest of the table in #473 lands with its
app: `T123` parents and subtasks, policy values, status and priority for
*this* instance, a column on the named board, and a project hashtag that is
already taken. Each is a new `Resolver` added to :data:`DEFAULT_RESOLVERS`;
nothing else here has to change.

**A resolver may cost a request; a colour costs none.** The colour keys are
fixed in Phorge's own source - `projects.colors` relabels a colour but
cannot add one - so `color:` is a `FieldKind.ENUM` in the registry and the
offline pass settles it. Nothing here asks about one. An **icon** is the
opposite: the icon set is `projects.icons` instance configuration that no
Conduit method reports, so the icons projects carry are only an
approximation of it and the server still accepts a configured icon no
project uses yet. :class:`IconResolver` therefore reports
`Severity.WARNING`, which is what keeps an exit status honest: a warning
says "this may be a typo", never "this is wrong".

The one seam left:

- `phabfive.spec.references` declares *which* keys hold a reference;
  `phabfive.spec.registry` declares what a key's value *is*, but for search
  fields and two project create fields so far. :data:`_CREATE_FIELD_KINDS`
  bridges the two until the create fields join the registry, at which point
  its create half goes away. Its ``"search"`` half stays a little longer: a
  `searches:` item's object type is ``"search"`` rather than one of the
  registry's, because what it searches is its own ``type:``, and the four
  user filters declared there mean a user whichever type that is.

The seam Phase 1 left open - that a resolver answered for a whole
`FieldKind` while `FieldKind.INSTANCE_ENUM` covers several different lookups
- is closed by :class:`ReferenceGroup`. Moving colour to `ENUM` removed one
of the four; status, priority and icon are told apart by the **field name**,
and the choice was to key the *registration* rather than the call:
a resolver declares which field names it answers for in `Resolver.fields`,
`validate_online` groups the index by `(kind, field)` for the kinds in
:data:`FIELD_SCOPED_KINDS` and by kind alone for every other, and each group
is still one call with every distinct value in it. Passing the field name
into `resolve()` instead would have turned every resolver into a re-grouper
and cost a request per field.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import logging
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Optional, Protocol

from phabfive.me import is_me
from phabfive.pagination import search_all_pages
from phabfive.spec.problems import Layer, Problem, Severity, problem
from phabfive.spec.references import Reference, RefKind, iter_references
from phabfive.spec.registry import OBJECT_TYPES, FieldKind, field_by_name
from phabfive.users import USER_PHID_PREFIX

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from phabfive.core import Phabfive
    from phabfive.spec.envelope import Spec

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RESOLVERS",
    "FIELD_SCOPED_KINDS",
    "REFERENCE_KINDS",
    "IconResolver",
    "ManiphestUserResolver",
    "ProjectResolver",
    "ReferenceGroup",
    "ReferenceIndex",
    "ResolveResult",
    "Resolver",
    "SpaceResolver",
    "field_kind",
    "field_name",
    "index_references",
    "reference_group",
    "validate_online",
]


#: The field kinds that cannot be settled without asking the instance. Every
#: other kind is a shape the offline pass checks from the value alone.
REFERENCE_KINDS = frozenset(
    {
        FieldKind.USER,
        FieldKind.PROJECT,
        FieldKind.SPACE,
        FieldKind.POLICY,
        FieldKind.MONOGRAM,
        FieldKind.INSTANCE_ENUM,
    }
)

# What each create-spec reference key names, for the keys the registry has
# not reached yet. `phabfive.spec.references` declares WHICH create keys hold
# a reference; the registry declares what a key's value IS - and it now does
# so for `("project", "create")`, which is why `icon:` is absent here and
# `field_kind` finds it in the registry instead. A task's create keys are
# still bridged here. Every entry goes away as its object type's fields join
# the registry.
_CREATE_FIELD_KINDS: Mapping[str, Mapping[str, FieldKind]] = {
    "task": {
        "assignment": FieldKind.USER,
        "subscribers": FieldKind.USER,
        "projects": FieldKind.PROJECT,
        "space": FieldKind.SPACE,
        "parents": FieldKind.MONOGRAM,
        "subtasks": FieldKind.MONOGRAM,
    },
    # A `searches:` item's object type is "search", not one of the registry's,
    # because what it searches is its own `type:`. These four keys mean a user
    # whichever type that is, so the kind is the same for all of them and the
    # item's own type does not have to be plumbed through here. A key that
    # ever means something different per searched type gets that plumbing
    # then, with the test that needs it.
    "search": {
        "assigned": FieldKind.USER,
        "author": FieldKind.USER,
        "subscriber": FieldKind.USER,
        "closed-by": FieldKind.USER,
    },
}

#: The kinds whose lookup is decided by the *field* as well as the kind.
#: `FieldKind.INSTANCE_ENUM` is one kind covering three different questions -
#: is this a status, is this a priority, is this an icon - and they are three
#: different requests. Every other kind is one lookup however many keys carry
#: it, so ``#infra`` named by both ``projects:`` and ``tag:`` stays one group
#: and therefore one `project.query`.
FIELD_SCOPED_KINDS = frozenset({FieldKind.INSTANCE_ENUM})

# An unrendered variable is not a reference. Reporting "{{ assignee }}" as an
# unknown user on top of "assignee is undefined" is two problems for one
# mistake, and the offline pass already owns the first one.
_UNRENDERED = "{{"


@dataclasses.dataclass(frozen=True)
class ReferenceGroup:
    """One lookup: every value asked about in one call.

    A group is a kind, plus the field name for the kinds in
    :data:`FIELD_SCOPED_KINDS` where the kind alone does not say which
    question is being asked. `field` is ``None`` for every other kind, which
    is what keeps one request serving every key that carries it.

    Attributes
    ----------
    kind : FieldKind
        What the values are.
    field : str or None
        The spec key, without its index - ``"parents[0]"`` is ``"parents"``.
        ``None`` means "every field of this kind".
    """

    kind: FieldKind
    field: Optional[str] = None

    def __str__(self) -> str:
        return (
            self.kind.value if self.field is None else f"{self.kind.value}:{self.field}"
        )


@dataclasses.dataclass(frozen=True)
class ResolveResult:
    """What a resolver made of one value.

    A result is either an answer or a failure, and the two are told apart by
    `problem` alone: a resolver that could not *ask* raises instead of
    returning a failure, so a failure always means the instance answered and
    the answer was no.

    Attributes
    ----------
    value : str
        The value that was asked about, exactly as the spec wrote it.
    phid : str or None
        What it resolved to, when it resolved. Carried so that a caller that
        validates and then acts does not look the same name up twice.
    problem : str or None
        The `Problem.code` to report when it did not resolve, e.g.
        "unknown-user". None means it resolved.
    reason : str or None
        The sentence to report. Optional: a resolver that leaves it out gets
        a generic one naming the value.
    candidates : tuple of str
        The several things an ambiguous value matched, for the message.
    severity : str
        `Severity.ERROR`, or `Severity.WARNING` for a lookup that cannot be
        conclusive. An icon is the case this exists for: no Conduit method
        reports the configured icon set, so an icon outside the observed one
        may still be one the server takes, and reporting it as an error
        would refuse a spec that would have applied. A warning is reported
        like any other problem and costs no exit status.
    """

    value: str
    phid: Optional[str] = None
    problem: Optional[str] = None
    reason: Optional[str] = None
    candidates: tuple[str, ...] = ()
    severity: str = Severity.ERROR

    @property
    def resolved(self) -> bool:
        """Whether the instance answered yes."""
        return self.problem is None


class Resolver(Protocol):
    """Resolves one kind of reference for one object type, in bulk.

    The bulk signature is the point: `validate_online` calls a resolver once
    with every distinct value of its kind in the whole spec, so a resolver
    makes one request where a per-item implementation would make one per
    item.

    A resolver **raises** when it could not ask - it lets
    `PhabfiveConnectionException` and `PhabfiveAPIException` out of
    `resolve` untouched - and **returns a failure** when it asked and the
    answer was no. Anything else reports a broken network as a broken spec.

    Attributes
    ----------
    kind : FieldKind
        The kind of reference this answers for.
    fields : frozenset of str
        The spec keys it answers for, when the kind alone is not enough -
        `FieldKind.INSTANCE_ENUM` covers a status, a priority and an icon,
        which are three different requests. Empty means every field of the
        kind, which is what all but the field-scoped kinds use. The
        registration carries this rather than the call, so the bulk contract
        - "called once with every distinct value in your group" - is
        unchanged.
    """

    kind: FieldKind
    fields: frozenset[str]

    def resolve(
        self, app: "Phabfive", values: Sequence[str]
    ) -> Mapping[str, ResolveResult]:
        """Answer every distinct value once.

        Parameters
        ----------
        app : Phabfive
            An already-constructed app. The resolver uses `app.phab` and
            never builds a client of its own.
        values : sequence of str
            Every distinct value of this resolver's kind, in the order the
            spec first named them.

        Returns
        -------
        mapping
            What was written to its :class:`ResolveResult`. A value left out
            of the mapping is reported by nobody, so a resolver answers for
            every value it was given.
        """
        ...  # pragma: no cover - protocol


class ReferenceIndex:
    """Every reference in a spec, in document order and deduplicated by value.

    Two views over one walk: `references` is document order, which is what
    makes the report deterministic and therefore testable, and the groups are
    the distinct values per :class:`ReferenceGroup`, which is what makes one
    request serve every item that named the same thing.

    Grouping is by kind alone for every kind but the ones in
    :data:`FIELD_SCOPED_KINDS`, which group by ``(kind, field)`` because one
    kind there covers several different lookups. `kinds`, `values` and `sites`
    are the kind-wide views over the same data, kept because a caller that
    only wants "every project this spec names" should not have to know which
    kinds are scoped.
    """

    def __init__(self, references: Sequence[Reference] = ()) -> None:
        self._references: tuple[Reference, ...] = tuple(references)
        self._groups_at: dict[int, ReferenceGroup] = {}

        # dict, not set: first-appearance order is the order values reach a
        # resolver, so a failing request names them the way the file does
        self._values: dict[ReferenceGroup, dict[str, list[Reference]]] = {}

        for position, reference in enumerate(self._references):
            group = reference_group(reference)

            if group is None:
                # Nothing online answers for it - a $local-id, or a key no
                # app has claimed yet. Kept in `references`, asked about by
                # nobody
                continue

            self._groups_at[position] = group
            by_value = self._values.setdefault(group, {})
            by_value.setdefault(reference.value, []).append(reference)

    @property
    def references(self) -> tuple[Reference, ...]:
        """Every reference, in the order the document wrote them."""
        return self._references

    def groups(self) -> tuple[ReferenceGroup, ...]:
        """The lookups present, in first-appearance order.

        One group is one call to one resolver, so this is also the number of
        resolvers `validate_online` will reach for.
        """
        return tuple(self._values)

    def values_in(self, group: ReferenceGroup) -> tuple[str, ...]:
        """The distinct values of one group, in first-appearance order."""
        return tuple(self._values.get(group, {}))

    def kinds(self) -> tuple[FieldKind, ...]:
        """The kinds present, in first-appearance order.

        A kind that is absent is never asked about, so a spec naming no user
        costs no `user.search`.
        """
        return tuple(dict.fromkeys(group.kind for group in self._values))

    def values(self, kind: FieldKind) -> tuple[str, ...]:
        """The distinct values of one kind, across its groups, in order."""
        return tuple(
            dict.fromkeys(
                value
                for group, values in self._values.items()
                if group.kind is kind
                for value in values
            )
        )

    def sites(self, kind: FieldKind, value: str) -> tuple[Reference, ...]:
        """Every place that named one value, in document order."""
        return tuple(
            reference
            for group, values in self._values.items()
            if group.kind is kind
            for reference in values.get(value, ())
        )

    def resolvable(self) -> Iterator[tuple[ReferenceGroup, Reference]]:
        """Every reference something online answers for, in document order.

        The group comes with it because `Reference.kind` is the *spelling* -
        `RefKind.NAME` for a bare word - while the lookup is decided by the
        field it sits in. See :func:`reference_group`.
        """
        for position, reference in enumerate(self._references):
            group = self._groups_at.get(position)

            if group is not None:
                yield group, reference

    def __len__(self) -> int:
        return len(self._references)

    def __bool__(self) -> bool:
        return bool(self._references)

    def __repr__(self) -> str:
        counts = ", ".join(
            f"{group}={len(values)}" for group, values in self._values.items()
        )
        return (
            f"<ReferenceIndex {len(self._references)} references: {counts or 'none'}>"
        )


def field_kind(reference: Reference) -> Optional[FieldKind]:
    """What one reference names, or None when nothing online can answer it.

    `phabfive.spec.references` classifies a value by its *spelling* -
    ``@alice`` is a user, ``alice`` is a name - which is not enough to know
    where to look it up: a bare name in ``assignment:`` is a user and the
    same word in ``projects:`` is a project. The owning field decides, which
    is why this reads the field and not `RefKind`.

    None means the online pass leaves it alone: a ``$local-id`` names
    something this spec creates rather than something the instance has, and
    that is entirely the offline pass's question.
    """
    if reference.kind is RefKind.LOCAL:
        return None

    name = field_name(reference)

    # Only "create": `iter_references` gives a search item the object type
    # "search", which is not a registry object type, so a search reference
    # never reaches this branch. When the search fields do, the verb comes
    # from the object type rather than being assumed here
    if reference.object_type in OBJECT_TYPES:
        declared = field_by_name(name, reference.object_type, "create")

        if declared is not None:
            return declared.kind

    return _CREATE_FIELD_KINDS.get(reference.object_type, {}).get(name)


def field_name(reference: Reference) -> str:
    """The key one reference sits in, without its index or its section.

    ``"parents[0]"`` is ``"parents"`` and a search spec's
    ``"search.assigned[1]"`` is ``"assigned"``. The index names *which*
    value and the section names *where* it is, both of which a problem
    reports; the key alone names *what kind of question* it is, which is
    what picks the resolver.
    """
    return reference.field.split("[", 1)[0].rsplit(".", 1)[-1]


def reference_group(reference: Reference) -> Optional[ReferenceGroup]:
    """Which lookup answers one reference, or None when nothing online does.

    The field name is carried only for :data:`FIELD_SCOPED_KINDS`. Carrying
    it for every kind would split ``projects:`` and ``tag:`` into two groups
    naming the same projects, and cost two requests to answer one question.
    """
    kind = field_kind(reference)

    if kind is None or kind not in REFERENCE_KINDS:
        return None

    if kind in FIELD_SCOPED_KINDS:
        return ReferenceGroup(kind=kind, field=field_name(reference))

    return ReferenceGroup(kind=kind)


def index_references(spec: "Spec") -> ReferenceIndex:
    """Every reference a spec makes that the instance has to answer for.

    The walk itself is `phabfive.spec.references.iter_references`, which is
    the one grammar both layers read; this adds only what the online pass
    needs on top - which kind of lookup each reference is, and the distinct
    values per kind.

    Parameters
    ----------
    spec : Spec
        The spec to walk. Read-only: nothing here mutates the body.

    Returns
    -------
    ReferenceIndex

    Notes
    -----
    A value still holding ``{{ ... }}`` is not indexed. Render the spec
    first when the variables are known; until then an undefined variable is
    the offline pass's problem and reporting it twice helps nobody.
    """
    return ReferenceIndex(
        [
            reference
            for reference in iter_references(spec)
            if _UNRENDERED not in reference.value
        ]
    )


def _reason(result: ResolveResult, reference: Reference) -> str:
    """The sentence to report when a resolver did not supply one."""
    if result.reason:
        return result.reason

    if result.candidates:
        listed = ", ".join(f"'{candidate}'" for candidate in result.candidates)
        return f"{reference.value!r} is ambiguous, it matches: {listed}"

    return f"{reference.value!r} does not name anything on this instance"


def validate_online(
    spec: "Spec",
    app: "Phabfive",
    *,
    resolvers: Optional[Sequence[Resolver]] = None,
) -> list[Problem]:
    """Check every reference a spec makes against the instance, all at once.

    Parameters
    ----------
    spec : Spec
        The spec to check. Render it first if it declares variables; an
        unrendered ``{{ ... }}`` is skipped rather than reported here.
    app : Phabfive
        An already-constructed app, used through `app.phab`. Nothing is
        constructed, no configuration is read, and nothing is written.
    resolvers : sequence of Resolver, optional
        The resolvers to use, in place of :data:`DEFAULT_RESOLVERS`. This is
        how an app registers its own kinds in a later phase, and how a test
        supplies a fake one.

    Returns
    -------
    list of Problem
        Every unresolvable reference, in document order, each with
        ``layer="online"``. Empty when everything resolved. **This never
        raises because the spec was wrong** - that is the whole point, and
        it is what lets a frontend render the report per field.

        A problem carries the severity its resolver gave it, so a report may
        hold warnings as well as errors and the caller counts them
        separately: `phabfive spec validate` exits on the errors alone.

    Raises
    ------
    PhabfiveRemoteException
        The instance could not be asked: `PhabfiveConnectionException` when
        the request never landed, `PhabfiveAPIException` when Conduit
        refused it. Neither is a bad reference and neither is converted into
        a problem - the caller decides what a failed check means.
    """
    index = index_references(spec)

    chosen = DEFAULT_RESOLVERS if resolvers is None else tuple(resolvers)
    registered = _register(chosen)

    answers: dict[ReferenceGroup, Mapping[str, ResolveResult]] = {}

    for group in index.groups():
        resolver = _resolver_for(registered, group)

        if resolver is None:
            # Not yet implemented rather than clean: say so, and report
            # nothing. Reporting a problem would be an invented verdict
            log.debug(
                f"No resolver for {group} references; "
                f"{len(index.values_in(group))} left unchecked"
            )
            continue

        answers[group] = resolver.resolve(app, index.values_in(group)) or {}

    problems: list[Problem] = []

    for group, reference in index.resolvable():
        answered = answers.get(group)

        if answered is None:
            continue

        result = answered.get(reference.value)

        if result is None:
            # A resolver that skipped a value it was handed. Not a verdict,
            # so it is not reported - but it is a bug worth saying out loud
            log.warning(
                f"Resolver for {group} did not answer for "
                f"{reference.value!r}; it is left unchecked"
            )
            continue

        if result.resolved:
            continue

        problems.append(
            problem(
                reference.object,
                field=reference.field,
                value=reference.value,
                reason=_reason(result, reference),
                code=result.problem or "unknown-reference",
                layer=Layer.ONLINE,
                severity=result.severity,
            )
        )

    return problems


def _register(
    resolvers: Sequence[Resolver],
) -> tuple[dict[tuple[FieldKind, str], Resolver], dict[FieldKind, Resolver]]:
    """The two tables a group is looked up in, most specific first.

    First registration wins in both, so a caller's own list is prepended to
    the defaults rather than merged with them.
    """
    by_field: dict[tuple[FieldKind, str], Resolver] = {}
    by_kind: dict[FieldKind, Resolver] = {}

    for resolver in resolvers:
        # getattr, not `resolver.fields`: the protocol declares it, but a
        # resolver written against the Phase 1 protocol has none, and a
        # caller's own resolver is not a reason to traceback.
        declared: frozenset[str] = getattr(resolver, "fields", frozenset())

        for name in declared:
            by_field.setdefault((resolver.kind, name), resolver)

        if not declared:
            by_kind.setdefault(resolver.kind, resolver)

    return by_field, by_kind


def _resolver_for(
    registered: tuple[dict[tuple[FieldKind, str], Resolver], dict[FieldKind, Resolver]],
    group: ReferenceGroup,
) -> Optional[Resolver]:
    """The resolver that answers one group: its field's, else its kind's."""
    by_field, by_kind = registered

    if group.field is not None:
        named = by_field.get((group.kind, group.field))

        if named is not None:
            return named

    return by_kind.get(group.kind)


def _search_users(app: "Phabfive", constraints: Mapping[str, Any]) -> list[Any]:
    """Every page of one `user.search`, with the remote failures left alone.

    `phabfive.users.resolve_user_phids` is the equivalent helper for a
    command, and it is deliberately not used here: it flattens every
    exception into `PhabfiveDataException`, which would make a connection
    failure indistinguishable from "no such user" - the one thing this layer
    must not do.

    Paged through `phabfive.pagination`, which is not optional here.
    Conduit answers a search with at most 100 rows however many were asked
    about, and this layer's whole point is that *every* distinct name in the
    spec goes into one request - so a spec naming 150 users would report 50
    of them as `unknown-user` if only the first page were read. That is a
    lookup only partly made, reported as an absence, which is the
    distinction this module exists to keep.
    """
    return search_all_pages(app.phab.user.search, constraints=dict(constraints))


def _username(record: Any) -> Optional[str]:
    """The username of a `user.search` record, when it has one."""
    if not isinstance(record, Mapping):
        return None

    fields = record.get("fields") or {}
    username = fields.get("username") if isinstance(fields, Mapping) else None

    return username if isinstance(username, str) else None


class ManiphestUserResolver:
    """`@me`, `@alice`, `alice` and `PHID-USER-...` in two requests at most.

    The spellings are the ones every phabfive option that takes a user
    accepts, so a spec and a flag mean the same thing by the same word - see
    `phabfive.users`, which is where the grammar is documented.

    Batched: every username in the spec goes into one `user.search`, every
    PHID into a second, and `whoami` is asked only when the spec says `@me`.
    A spec naming the same user in forty tasks costs the same as one naming
    them once.

    `@me` on an instance that also has a user called "me" is a problem, not
    an error: `@me` then names two people, and `phabfive.me` refuses to
    guess for exactly the same reason. It is reported as `ambiguous-user` so
    the rest of the spec is still checked in the same run.
    """

    kind: FieldKind = FieldKind.USER
    fields: frozenset[str] = frozenset()

    def resolve(
        self, app: "Phabfive", values: Sequence[str]
    ) -> Mapping[str, ResolveResult]:
        """Answer every user the spec named. See :class:`Resolver`."""
        results: dict[str, ResolveResult] = {}

        me_values = [value for value in values if is_me(value)]
        phid_values = [
            value
            for value in values
            if not is_me(value) and value.startswith(USER_PHID_PREFIX)
        ]
        named = {
            value: value[1:] if value.startswith("@") else value
            for value in values
            if not is_me(value) and not value.startswith(USER_PHID_PREFIX)
        }

        wanted = {name.casefold() for name in named.values()}

        if me_values:
            # Asked in the same request rather than a third one: a user
            # actually called "me" is what makes @me ambiguous
            wanted.add("me")

        found: dict[str, Any] = {}

        if wanted:
            for record in _search_users(app, {"usernames": sorted(wanted)}):
                username = _username(record)

                if username:
                    found[username.casefold()] = record

        if me_values:
            results.update(self._resolve_me(app, me_values, found.get("me")))

        for value, name in named.items():
            record = found.get(name.casefold())

            if record is None:
                results[value] = ResolveResult(
                    value=value,
                    problem="unknown-user",
                    reason=f"No such user: {value!r}",
                )
            else:
                results[value] = ResolveResult(value=value, phid=record.get("phid"))

        if phid_values:
            results.update(self._resolve_phids(app, phid_values))

        return results

    def _resolve_me(
        self, app: "Phabfive", values: Sequence[str], other: Any
    ) -> dict[str, ResolveResult]:
        """`@me`, and the one instance where it does not have an answer."""
        if other is not None:
            username = _username(other) or "me"
            return {
                value: ResolveResult(
                    value=value,
                    problem="ambiguous-user",
                    reason=(
                        f"{value!r} names both you and the user {username!r} on "
                        f"this instance; use a username or a PHID"
                    ),
                    candidates=(username,),
                )
                for value in values
            }

        whoami = app.phab.user.whoami() or {}
        phid = whoami.get("phid") if isinstance(whoami, Mapping) else None

        if not phid:
            return {
                value: ResolveResult(
                    value=value,
                    problem="unknown-user",
                    reason=f"{value!r} did not resolve: the instance gave you no PHID",
                )
                for value in values
            }

        return {value: ResolveResult(value=value, phid=phid) for value in values}

    def _resolve_phids(
        self, app: "Phabfive", values: Sequence[str]
    ) -> dict[str, ResolveResult]:
        """User PHIDs, asked about rather than passed through.

        A mistyped PHID that is never checked looks like "no matches" much
        later, in a search result that quietly excluded somebody.
        """
        found = {
            record["phid"]: record
            for record in _search_users(app, {"phids": sorted(set(values))})
            if isinstance(record, Mapping) and record.get("phid")
        }

        return {
            value: (
                ResolveResult(value=value, phid=value)
                if value in found
                else ResolveResult(
                    value=value,
                    problem="unknown-user",
                    reason=f"No such user: {value!r}",
                )
            )
            for value in values
        }


# --------------------------------------------------------------------------
# Projects, Spaces and icons
#
# Each of the three imports its lookup from `phabfive.maniphest.resolvers`
# or `phabfive.project.core` INSIDE `resolve`, never at module level:
# importing either reaches `phabfive.core`, `phabricator` and `requests`,
# and `tests/test_spec_isolation.py` imports every module of this subpackage
# in a fresh interpreter and asserts none of the three arrived. The offline
# path must stay free of them; the online path is welcome to pay for them
# the moment it actually asks the instance something.
# --------------------------------------------------------------------------


class ProjectResolver:
    """``#hashtag``, ``Name``, ``8048`` and ``PHID-PROJ-...``, in one request.

    One `project.query` enumerates every project the viewer can see, keyed
    by lowercased name **and** by every slug, which is
    `fetch_project_lookup_maps` - the same map `maniphest create` resolves
    `projects:` through, so a spec that validates resolves to the same PHIDs
    when it is applied.

    **Ambiguity is not absence.** Several projects can share a name -
    milestones called "Sprint 1" in different parents - and
    `fetch_project_lookup_maps` deliberately leaves such a name out of the
    map rather than picking one. Reporting that as "no such project" would
    send somebody looking for a project that is right there, so it is
    `ambiguous-project`, with the candidates named:
    `ambiguous_project_message` already writes that sentence and this does
    not write a second one.
    """

    kind: FieldKind = FieldKind.PROJECT
    fields: frozenset[str] = frozenset()

    def resolve(
        self, app: "Phabfive", values: Sequence[str]
    ) -> Mapping[str, ResolveResult]:
        """Answer every project the spec named. See :class:`Resolver`."""
        from phabfive.maniphest.resolvers import (
            PROJECT_PHID_PREFIX,
            fetch_project_lookup_maps,
        )

        name_to_phid, _, ambiguous_names = fetch_project_lookup_maps(app.phab)

        # Every project came back, so a PHID is answered from the same walk
        # rather than from a second request. An ambiguous name is left out of
        # name_to_phid but its projects are real, so their PHIDs count too.
        known_phids = set(name_to_phid.values())
        known_phids.update(phid for phids in ambiguous_names.values() for phid in phids)

        results: dict[str, ResolveResult] = {}
        ambiguous: dict[str, list[str]] = {}
        numeric: list[str] = []

        for value in values:
            key = (value[1:] if value.startswith("#") else value).casefold()

            if value.startswith(PROJECT_PHID_PREFIX):
                results[value] = (
                    ResolveResult(value=value, phid=value)
                    if value in known_phids
                    else self._missing(value)
                )
            elif "*" in value:
                results[value] = self._wildcard(value, name_to_phid, ambiguous_names)
            elif key in name_to_phid:
                results[value] = ResolveResult(value=value, phid=name_to_phid[key])
            elif key in ambiguous_names:
                ambiguous[value] = list(ambiguous_names[key])
            elif value.isascii() and value.isdigit():
                # A numeric project id is the one spelling the enumeration
                # does not key, so it costs one more request - for every id
                # in the spec at once, not one each.
                numeric.append(value)
            else:
                results[value] = self._missing(value)

        if ambiguous:
            results.update(self._describe_ambiguous(app, ambiguous))

        if numeric:
            results.update(self._resolve_ids(app, numeric))

        return results

    @staticmethod
    def _missing(value: str) -> ResolveResult:
        return ResolveResult(
            value=value,
            problem="unknown-project",
            reason=f"No such project: {value!r}",
        )

    @staticmethod
    def _wildcard(
        value: str,
        name_to_phid: Mapping[str, str],
        ambiguous_names: Mapping[str, Sequence[str]],
    ) -> ResolveResult:
        """A pattern resolves when it matches something, and names no PHID.

        `tag: team-*` is a filter over several projects rather than one
        project, so there is no single PHID to carry forward - only the fact
        that it matched. A pattern that matches nothing is reported, because
        a search filtered by it silently returns nothing at all.

        This answers only for the keys that *mean* something by a glob.
        `projects:` on a create item does not - `maniphest create` refuses a
        wildcard there with no network - and
        `phabfive.spec.references.ReferenceField.patterns` says which keys
        are which, so the offline pass reports such a value before this is
        ever reached. Blessing one here is what made a create spec validate
        clean and then be refused when it was applied.
        """
        pattern = value.casefold()
        keys = list(name_to_phid) + list(ambiguous_names)

        if any(fnmatch.fnmatch(key, pattern) for key in keys):
            return ResolveResult(value=value)

        return ResolveResult(
            value=value,
            problem="unknown-project",
            reason=f"No project matches the pattern {value!r}",
        )

    @staticmethod
    def _describe_ambiguous(
        app: "Phabfive", ambiguous: Mapping[str, Sequence[str]]
    ) -> dict[str, ResolveResult]:
        """One `project.search` describes every ambiguous name at once."""
        from phabfive.maniphest.resolvers import (
            ambiguous_project_message,
            fetch_projects_by_phid,
        )

        wanted = sorted({phid for phids in ambiguous.values() for phid in phids})
        # Best effort, and deliberately so: this is only how the candidates
        # are *described*. The verdict was already reached from the map, so a
        # failure here costs a nicer sentence and never a wrong answer.
        described = {
            record["phid"]: record
            for record in fetch_projects_by_phid(app.phab, wanted)
            if isinstance(record, Mapping) and record.get("phid")
        }

        results: dict[str, ResolveResult] = {}

        for value, phids in ambiguous.items():
            matches = [described.get(phid, phid) for phid in phids]
            results[value] = ResolveResult(
                value=value,
                problem="ambiguous-project",
                reason=ambiguous_project_message(value, matches),
                candidates=tuple(_project_label(match) for match in matches),
            )

        return results

    @staticmethod
    def _resolve_ids(
        app: "Phabfive", values: Sequence[str]
    ) -> dict[str, ResolveResult]:
        """Numeric project ids, every one of them in one `project.search`.

        `lookup_project_by_id` is the equivalent helper for a command and is
        deliberately not used: it answers a failed request with ``None``,
        which this layer would report as "no such project" - a broken
        network read as a broken spec.
        """
        found = {
            str(record["id"]): record
            for record in search_all_pages(
                app.phab.project.search,
                constraints={"ids": [int(value) for value in values]},
            )
            if isinstance(record, Mapping) and record.get("id") is not None
        }

        return {
            value: (
                ResolveResult(value=value, phid=found[value].get("phid"))
                if value in found
                else ProjectResolver._missing(value)
            )
            for value in values
        }


def _project_label(match: Any) -> str:
    """One ambiguity candidate, named the way Phorge's web UI names it."""
    if not isinstance(match, Mapping):
        return str(match)

    fields = match.get("fields") or {}
    name = fields.get("name") if isinstance(fields, Mapping) else None
    parent = fields.get("parent") if isinstance(fields, Mapping) else None

    if isinstance(parent, Mapping) and parent.get("name"):
        return f"{name} ({parent['name']})"

    return str(name or match.get("phid") or match)


class SpaceResolver:
    """``S3``, a Space's name, or a pattern naming exactly one.

    Every Space the viewer can see is enumerated once - `fetch_all_spaces`,
    which probes the monogram range because `phid.lookup` is policy-filtered
    and the visible monograms are therefore sparse - and every value in the
    spec is then resolved against that one answer with no further request.

    `resolve_space` is what does the resolving, so a spec and
    `maniphest create --space` agree by construction, including the rule
    that a pattern matching two Spaces is an error rather than a silent pick
    of the first: an object goes in exactly one Space.
    """

    kind: FieldKind = FieldKind.SPACE
    fields: frozenset[str] = frozenset()

    def resolve(
        self, app: "Phabfive", values: Sequence[str]
    ) -> Mapping[str, ResolveResult]:
        """Answer every Space the spec named. See :class:`Resolver`."""
        from phabfive.exceptions import PhabfiveConfigException
        from phabfive.maniphest.resolvers import fetch_all_spaces, resolve_space

        # Raises PhabfiveRemoteException when the probe failed, which is the
        # right thing: a partial Space list would report real Spaces as
        # missing.
        all_spaces = fetch_all_spaces(app.phab)

        results: dict[str, ResolveResult] = {}

        for value in values:
            try:
                entry = resolve_space(app.phab, value, all_spaces=all_spaces)
            except PhabfiveConfigException as failure:
                # The instance answered and the answer was no - either
                # nothing matched or several did. `resolve_space` has
                # already written the sentence, including the Spaces the
                # viewer can see, so it is reported rather than restated.
                results[value] = ResolveResult(
                    value=value,
                    problem="unknown-space",
                    reason=str(failure),
                )
            else:
                results[value] = ResolveResult(value=value, phid=entry.get("phid"))

        return results


class IconResolver:
    """A project icon, which can only ever be a **warning**.

    `projects.icons` is instance configuration and no Conduit method reports
    it, so the closest the API comes to naming a custom icon is the set of
    icons projects actually carry. An icon that is configured but that no
    project uses yet is indistinguishable from a typo - and the server takes
    it. Refusing one would refuse a spec that would have applied, so an
    unrecognised icon is `unknown-icon` at `Severity.WARNING`: reported,
    counted as a warning, and costing no exit status.

    Field-scoped, because `FieldKind.INSTANCE_ENUM` is also what a status
    and a priority are: `fields` is what says this one answers for `icon:`
    and leaves the other two to their own resolvers.
    """

    kind: FieldKind = FieldKind.INSTANCE_ENUM
    fields: frozenset[str] = frozenset({"icon"})

    def resolve(
        self, app: "Phabfive", values: Sequence[str]
    ) -> Mapping[str, ResolveResult]:
        """Answer every icon the spec named. See :class:`Resolver`."""
        from phabfive.constants import PROJECT_MILESTONE_ICON
        from phabfive.project.core import icons_in_use

        doubtful = [value for value in values if value not in _stock_icons()]

        if not doubtful:
            return {value: ResolveResult(value=value) for value in values}

        # One `project.search` over every project, however many icons the
        # spec names - and only when at least one of them was not a stock
        # icon, so the common spec costs nothing at all.
        known = set(icons_in_use(app.phab))
        # Phorge gives every milestone this icon whatever is stored on it, so
        # it is a real key that may well appear on no project at all.
        known.add(PROJECT_MILESTONE_ICON)

        return {
            value: (
                ResolveResult(value=value)
                if value not in doubtful or value in known
                else ResolveResult(
                    value=value,
                    problem="unknown-icon",
                    reason=(
                        f"No project uses the icon {value!r} and it is not one "
                        "Phorge ships, so it may be misspelled. An icon "
                        "configured in projects.icons that no project uses yet "
                        "cannot be checked."
                    ),
                    severity=Severity.WARNING,
                )
            )
            for value in values
        }


def _stock_icons() -> frozenset[str]:
    """The icons Phorge ships, which need no request to recognise."""
    from phabfive.constants import PROJECT_ICONS

    return frozenset(PROJECT_ICONS)


#: The resolvers `validate_online` uses when the caller names none. Phase 1
#: shipped the user resolver; Phase 2 adds projects, Spaces and icons.
#: Phase 3 adds theirs here as each app lands.
DEFAULT_RESOLVERS: tuple[Resolver, ...] = (
    ManiphestUserResolver(),
    ProjectResolver(),
    SpaceResolver(),
    IconResolver(),
)
