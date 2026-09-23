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

Phase 1 ships the protocol, the index, the atomic collection and exactly one
resolver - :class:`ManiphestUserResolver`, which proves the protocol end to
end. The rest of the table in #473 lands with its app in Phases 2 and 3:
projects and their ambiguity, spaces by monogram/name/pattern, `T123`
parents and subtasks, policy values, status and priority for *this*
instance, a column on the named board, and a project hashtag that is already
taken. Each is a new `Resolver` added to :data:`DEFAULT_RESOLVERS`; nothing
else here has to change.

Two seams are worth naming now, so that the next phase is not surprised:

- A resolver answers for a whole `FieldKind`, and `FieldKind.INSTANCE_ENUM`
  covers status, priority, icon and colour, which are four different
  lookups. The kind alone does not say which, so whoever answers for it will
  need the field name too. Left open rather than guessed at here.
- `phabfive.spec.references` declares *which* create keys hold a reference;
  `phabfive.spec.registry` declares what a key's value *is*, but for search
  fields only so far. :data:`_CREATE_FIELD_KINDS` bridges the two until the
  create fields join the registry, at which point it goes away - a search
  spec therefore has no online references in Phase 1, because
  `references.REFERENCE_FIELDS` has no entry for a search item yet.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Optional, Protocol

from phabfive.me import is_me
from phabfive.pagination import search_all_pages
from phabfive.spec.problems import Layer, Problem, problem
from phabfive.spec.references import Reference, RefKind, iter_references
from phabfive.spec.registry import OBJECT_TYPES, FieldKind, field_by_name
from phabfive.users import USER_PHID_PREFIX

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from phabfive.core import Phabfive
    from phabfive.spec.envelope import Spec

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RESOLVERS",
    "REFERENCE_KINDS",
    "ManiphestUserResolver",
    "ReferenceIndex",
    "ResolveResult",
    "Resolver",
    "field_kind",
    "index_references",
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

# What each create-spec reference key names. `phabfive.spec.references`
# declares WHICH create keys hold a reference; the registry declares what a
# key's value IS, but only for search fields so far. Until the create fields
# join the registry - `references.py` says in so many words that its table
# moves there then - this is the one bridge between the two, and it is the
# whole of what a Phase 2 app has to add here.
_CREATE_FIELD_KINDS: Mapping[str, Mapping[str, FieldKind]] = {
    "task": {
        "assignment": FieldKind.USER,
        "subscribers": FieldKind.USER,
        "projects": FieldKind.PROJECT,
        "space": FieldKind.SPACE,
        "parents": FieldKind.MONOGRAM,
        "subtasks": FieldKind.MONOGRAM,
    },
}

# An unrendered variable is not a reference. Reporting "{{ assignee }}" as an
# unknown user on top of "assignee is undefined" is two problems for one
# mistake, and the offline pass already owns the first one.
_UNRENDERED = "{{"


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
    """

    value: str
    phid: Optional[str] = None
    problem: Optional[str] = None
    reason: Optional[str] = None
    candidates: tuple[str, ...] = ()

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
    """

    kind: FieldKind

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
    makes the report deterministic and therefore testable, and `values` is
    the distinct set per kind, which is what makes one request serve every
    item that named the same thing.
    """

    def __init__(self, references: Sequence[Reference] = ()) -> None:
        self._references: tuple[Reference, ...] = tuple(references)
        self._kinds: dict[int, FieldKind] = {}

        # dict, not set: first-appearance order is the order values reach a
        # resolver, so a failing request names them the way the file does
        self._values: dict[FieldKind, dict[str, list[Reference]]] = {}

        for position, reference in enumerate(self._references):
            kind = field_kind(reference)

            if kind is None or kind not in REFERENCE_KINDS:
                # Nothing online answers for it - a $local-id, or a key no
                # app has claimed yet. Kept in `references`, asked about by
                # nobody
                continue

            self._kinds[position] = kind
            by_value = self._values.setdefault(kind, {})
            by_value.setdefault(reference.value, []).append(reference)

    @property
    def references(self) -> tuple[Reference, ...]:
        """Every reference, in the order the document wrote them."""
        return self._references

    def kinds(self) -> tuple[FieldKind, ...]:
        """The kinds present, in first-appearance order.

        A kind that is absent is never asked about, so a spec naming no user
        costs no `user.search`.
        """
        return tuple(self._values)

    def values(self, kind: FieldKind) -> tuple[str, ...]:
        """The distinct values of one kind, in first-appearance order."""
        return tuple(self._values.get(kind, {}))

    def sites(self, kind: FieldKind, value: str) -> tuple[Reference, ...]:
        """Every place that named one value, in document order."""
        return tuple(self._values.get(kind, {}).get(value, ()))

    def resolvable(self) -> Iterator[tuple[FieldKind, Reference]]:
        """Every reference something online answers for, in document order.

        The kind comes with it because `Reference.kind` is the *spelling* -
        `RefKind.NAME` for a bare word - while the lookup is decided by the
        field it sits in. See :func:`field_kind`.
        """
        for position, reference in enumerate(self._references):
            kind = self._kinds.get(position)

            if kind is not None:
                yield kind, reference

    def __len__(self) -> int:
        return len(self._references)

    def __bool__(self) -> bool:
        return bool(self._references)

    def __repr__(self) -> str:
        counts = ", ".join(
            f"{kind.value}={len(values)}" for kind, values in self._values.items()
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

    # "parents[0]" is the key "parents"
    name = reference.field.split("[", 1)[0]

    # Only "create": `iter_references` gives a search item the object type
    # "search", which is not a registry object type, so a search reference
    # never reaches this branch. When the search fields do, the verb comes
    # from the object type rather than being assumed here
    if reference.object_type in OBJECT_TYPES:
        declared = field_by_name(name, reference.object_type, "create")

        if declared is not None:
            return declared.kind

    return _CREATE_FIELD_KINDS.get(reference.object_type, {}).get(name)


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

    by_kind: dict[FieldKind, Resolver] = {}
    for resolver in chosen:
        # First registration wins, so a caller's own list is prepended
        by_kind.setdefault(resolver.kind, resolver)

    answers: dict[FieldKind, Mapping[str, ResolveResult]] = {}

    for kind in index.kinds():
        for_kind = by_kind.get(kind)

        if for_kind is None:
            # Not yet implemented rather than clean: say so, and report
            # nothing. Reporting a problem would be an invented verdict
            log.debug(
                f"No resolver for {kind.value} references; "
                f"{len(index.values(kind))} left unchecked"
            )
            continue

        answers[kind] = for_kind.resolve(app, index.values(kind)) or {}

    problems: list[Problem] = []

    for kind, reference in index.resolvable():
        answered = answers.get(kind)

        if answered is None:
            continue

        result = answered.get(reference.value)

        if result is None:
            # A resolver that skipped a value it was handed. Not a verdict,
            # so it is not reported - but it is a bug worth saying out loud
            log.warning(
                f"Resolver for {kind.value} did not answer for "
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
            )
        )

    return problems


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


#: The resolvers `validate_online` uses when the caller names none. Phase 1
#: ships one; Phases 2 and 3 add theirs here as each app lands.
DEFAULT_RESOLVERS: tuple[Resolver, ...] = (ManiphestUserResolver(),)
