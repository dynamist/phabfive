# -*- coding: utf-8 -*-
"""The envelope a spec may carry, and the `Spec` object the rest of phabfive holds.

A spec describes Phorge objects. phabfive is one program that happens to read
one; another tool could write or consume the same file, which is why the
format is named after the domain rather than the tool::

    spec: phorge/v1alpha1      # optional; which revision of the format this is
    kind: create               # optional; inferred from the body when absent
    metadata:                  # optional; describes the FILE
      name: sprint-setup
      description: Standard task set for a new sprint
      version: "2.1"
      author: alice
    tasks:
      - title: Write the runbook

`kind` carries the **verb**, not the object type, so one document creates
tasks and projects together. A search spec names its target per item with
`type:`, which defaults to `task` - what every search template in the tree
means today.

Two things that are easily confused and must not be merged: `metadata:`
describes the file, while the `title:`/`description:` at the top of a search
template describe **one search's result banner** (see
`phabfive/cli/maniphest.py`, the banner printed per search config). They are
different things, and both survive: the banner keys become fields of a
`searches:` item.

Every envelope key is optional, because a spec should not have to say what
is already obvious from its body. `kind` is inferred - `search`/`searches`
means search, `tasks`/`projects`/`pastes` means create - and a file that
could be either is refused rather than guessed. It is what lets the loader
read the templates written before this format existed, though that is a
convenience rather than a promise: the format is v1alpha1 and free to churn. A caller that already knows what it
holds says so with `kind=`, which is what makes the legacy call sites safe: a
search template may legally carry only `title:` and `description:` with no
`search:` key at all, which inference cannot tell from a create spec.

`phorge/v1alpha1` is deliberately unstable. Keys may be renamed or dropped
between releases with no deprecation cycle, and there is no deprecation
machinery here on purpose: a spec naming an unknown or older version is
refused **by name**, never half-parsed. `phorge/v1` is promoted only once
every object type and both kinds have settled.

This module is library code: it does not print, prompt, exit, read the
environment or read configuration, and it imports nothing from phabfive but
`phabfive.exceptions`. It is imported *by* `phabfive.spec.loader` and never
imports it back.
"""

from __future__ import annotations

import copy
import dataclasses
import enum
import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Union

from phabfive.exceptions import PhabfiveDataException, PhabfiveInputException
from phabfive.spec.problems import Problem

log = logging.getLogger(__name__)

#: The one spec format revision this phabfive reads, named after the domain.
SPEC_VERSION = "phorge/v1alpha1"

#: Every revision accepted. A spec naming anything else is refused by name.
SUPPORTED_SPEC_VERSIONS = frozenset({SPEC_VERSION})

#: The keys that belong to the envelope rather than to the body.
ENVELOPE_KEYS = frozenset({"spec", "kind", "metadata"})

#: The `metadata:` keys with a meaning. Anything else is kept in
#: `Metadata.extra`, where the offline validation pass reports it as a
#: warning rather than losing it.
METADATA_KEYS = ("name", "description", "version", "author")

#: The declared variables, which are neither envelope nor body.
VARIABLES_KEY = "variables"

#: What a spec built from data rather than a file calls itself, in an error
#: message. One definition, exported, because `phabfive.spec.loader` writes
#: the same string as a default and the two disagreeing would show up as a
#: message naming the wrong thing.
DATA_SOURCE = "<data>"

# The top-level keys of the legacy search shape: one search, written flat.
# `title` and `description` are that search's result banner, not the file's.
LEGACY_SEARCH_KEYS = ("title", "description", "search")


class Kind(enum.Enum):
    """What a spec asks for. The verb, never the object type.

    `.value` is what JSON output and `kind:` carry.
    """

    CREATE = "create"
    SEARCH = "search"


# The body keys that hold items, per kind. These are what a multi-document
# file folds into: three YAML documents each holding one search become one
# `searches:` list of three, which is the shape every format can express.
# `passphrases` is one of them although nothing creates a passphrase: it is
# a create key phabfive recognises in order to *refuse* it, because Phorge
# exposes no `passphrase.edit` (see
# `phabfive.spec.references.UNCREATABLE_OBJECT_KEYS`). It folds and merges
# like any other item key so that a two-document file carrying one in each
# is refused for both rather than quietly keeping the last - a fold that
# drops a section is silent loss whatever happens to the section next.
_ITEM_KEYS: dict[Kind, tuple[str, ...]] = {
    Kind.CREATE: ("tasks", "projects", "pastes", "passphrases"),
    Kind.SEARCH: ("searches",),
}

# The item keys a document may be *offered*, which is the item keys minus
# the ones that exist only to be refused. Recognising `passphrases:` is what
# lets a file holding nothing else reach the offline pass and be told why,
# instead of being told by the loader that it does not say what kind it is -
# but a reader who has written no kind: should not be pointed at it.
_OFFERED_KEYS: dict[Kind, tuple[str, ...]] = {
    Kind.CREATE: ("tasks", "projects", "pastes"),
    Kind.SEARCH: ("search", "searches"),
}

# Which body keys mean which kind, for inference. "search" (singular) is the
# legacy flat shape; "searches" is the normalized one.
_KIND_KEYS: dict[Kind, tuple[str, ...]] = {
    Kind.SEARCH: ("search", "searches"),
    Kind.CREATE: _ITEM_KEYS[Kind.CREATE],
}

# What `spec.items("task")` means, for both spellings of every object type.
_BODY_KEY_BY_OBJECT = {
    "task": "tasks",
    "tasks": "tasks",
    "project": "projects",
    "projects": "projects",
    "paste": "pastes",
    "pastes": "pastes",
    "search": "searches",
    "searches": "searches",
}

#: What a `searches:` item means when it does not say.
DEFAULT_SEARCH_TYPE = "task"

KindLike = Union[Kind, str]
Document = Mapping[str, Any]


@dataclasses.dataclass(frozen=True)
class Metadata:
    """What a spec says about itself. Describes the FILE, never one item.

    Every field is optional, and a value is kept exactly as it was written:
    `version: 2.1` is a YAML float and a real trap, and the offline
    validation pass is what reports it, because silently coercing it to
    "2.1" would hide the mistake from the person who has to fix the file.
    """

    name: str | None = None
    description: str | None = None
    version: str | None = None
    author: str | None = None
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_data(cls, data: Any, *, source: str = DATA_SOURCE) -> Metadata:
        """Build metadata from a `metadata:` mapping, or from nothing.

        An unknown key is kept in `extra` rather than dropped or refused:
        it is a warning for the offline pass, not an error, and a file
        round-trips through `Spec.to_data` without losing it.
        """
        if data is None:
            return cls()

        if not isinstance(data, Mapping):
            raise PhabfiveDataException(
                f"metadata: in {source} is a {type(data).__name__}, not a mapping "
                f"of {', '.join(METADATA_KEYS)}"
            )

        extra = {
            str(key): value for key, value in data.items() if key not in METADATA_KEYS
        }
        if extra:
            log.debug("Unknown metadata key(s) in %s: %s", source, ", ".join(extra))

        return cls(
            name=data.get("name"),
            description=data.get("description"),
            version=data.get("version"),
            author=data.get("author"),
            extra=extra,
        )

    def as_data(self) -> dict[str, Any]:
        """The `metadata:` mapping again, without the keys nobody set."""
        record = {
            key: getattr(self, key)
            for key in METADATA_KEYS
            if getattr(self, key) is not None
        }
        record.update(copy.deepcopy(dict(self.extra)))
        return record


@dataclasses.dataclass(frozen=True)
class Envelope:
    """A spec's format version, kind and metadata. Every part optional."""

    spec: str | None = None
    """"phorge/v1alpha1" exactly as written, or None when the file said nothing."""

    kind: Kind = Kind.CREATE
    """Never None: supplied by the caller, declared by the file, or inferred."""

    kind_declared: bool = False
    """Whether `kind:` was actually written, as opposed to inferred or supplied."""

    metadata: Metadata = dataclasses.field(default_factory=lambda: Metadata())
    """What the file says about itself; empty when it says nothing."""


def _kind_names() -> str:
    """The kinds a message offers, in a stable order."""
    return ", ".join(member.value for member in Kind)


def as_kind(kind: KindLike, *, where: str = "kind") -> Kind:
    """A `Kind`, or the name of one, as a `Kind`.

    Raises
    ------
    PhabfiveInputException
        When `kind` is neither "create" nor "search". A kind is a choice from
        a closed set, so naming the wrong one is a bad argument value rather
        than bad data - even when it was read from a file.
    """
    if isinstance(kind, Kind):
        return kind

    if isinstance(kind, str):
        for member in Kind:
            if member.value == kind.strip().lower():
                return member

    raise PhabfiveInputException(
        f"Unknown spec {where} {kind!r}. A spec is one of: {_kind_names()}"
    )


def _documents(data: Any, *, source: str = DATA_SOURCE) -> list[dict[str, Any]]:
    """One mapping, or a sequence of them, as a list of documents.

    The caller's data is deep-copied here and nowhere else, so handing the
    same dict to `Spec.from_data` twice is safe and the caller's object is
    never mutated - exactly what `create_tasks_from_config` already promises.
    """
    if isinstance(data, Mapping):
        candidates: list[Any] = [data]
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        candidates = list(data)
    else:
        raise PhabfiveDataException(
            f"A spec is a mapping, or a list of mappings for a multi-document "
            f"file, not a {type(data).__name__} ({source})"
        )

    if not candidates:
        raise PhabfiveDataException(f"{source} holds no spec document")

    for index, document in enumerate(candidates, start=1):
        if not isinstance(document, Mapping):
            position = "" if len(candidates) == 1 else f" {index}"
            raise PhabfiveDataException(
                f"Document{position} in {source} is a {type(document).__name__} "
                f"at the root level, not a mapping"
            )

    return [copy.deepcopy(dict(document)) for document in candidates]


def _declared(
    documents: Sequence[Document], key: str, *, source: str
) -> tuple[Any, bool]:
    """The one value the documents declare for an envelope key.

    Several documents describe one file, and a file cannot be two kinds, so a
    second document declaring a *different* `spec:`, `kind:` or `metadata:`
    is refused. Repeating the same value is accepted - a generator writing
    one envelope per document is not doing anything wrong.
    """
    found: list[Any] = [document[key] for document in documents if key in document]

    if not found:
        return None, False

    first = found[0]
    for other in found[1:]:
        if other != first:
            raise PhabfiveDataException(
                f"The documents in {source} disagree about {key}: {first!r} and "
                f"{other!r}. Several documents describe one file, so its "
                f"envelope is written once"
            )

    return first, True


def infer_kind(
    document: Document | Iterable[Document], *, source: str = DATA_SOURCE
) -> Kind:
    """Work out whether a spec creates or searches, from its body keys alone.

    A declared `kind:` is not inference and is not looked at here; pass the
    body, or the whole document, either way.

    Parameters
    ----------
    document : mapping or iterable of mappings
        One document, or every document of a multi-document file - the keys
        of all of them are considered together.
    source : str, optional
        What to call the spec in an error message.

    Returns
    -------
    Kind

    Raises
    ------
    PhabfiveDataException
        When both key families are present - the file could be either, and
        guessing is worse than asking - or when neither is.
    """
    documents = [document] if isinstance(document, Mapping) else list(document)

    keys: set[str] = set()
    for one in documents:
        keys |= set(one)

    present = {
        kind: [key for key in candidates if key in keys]
        for kind, candidates in _KIND_KEYS.items()
    }
    matched = [kind for kind, found in present.items() if found]

    if len(matched) > 1:
        both = "; ".join(
            f"{kind.value}: {', '.join(present[kind])}"
            for kind in sorted(matched, key=lambda k: k.value)
        )
        raise PhabfiveDataException(
            f"{source} could be either kind of spec ({both}). Say which it is "
            f"with kind: {_kind_names()}"
        )

    if matched:
        return matched[0]

    looked_for = ", ".join(
        key
        for kind in sorted(_OFFERED_KEYS, key=lambda k: k.value)
        for key in _OFFERED_KEYS[kind]
    )
    raise PhabfiveDataException(
        f"{source} does not say what kind of spec it is, and has none of the "
        f"keys that would tell: {looked_for}. Add kind: {_kind_names()}"
    )


def _search_item(item: Any, *, source: str) -> dict[str, Any]:
    """One `searches:` item, with its defaults filled in.

    `type:` defaults to "task", which is what every search template in the
    tree means today, and `search:` to an empty mapping, which is the "no
    constraints" a template carrying only a banner has always had.
    """
    if not isinstance(item, Mapping):
        raise PhabfiveDataException(
            f"A searches: item in {source} is a {type(item).__name__}, not a mapping"
        )

    # `type` first, so a rendered item reads the way the documentation
    # writes one, whether or not the file spelled it out
    normalized: dict[str, Any] = {"type": DEFAULT_SEARCH_TYPE}
    normalized.update(item)
    normalized.setdefault("search", {})
    return normalized


def _items(value: Any, key: str, *, source: str) -> list[Any]:
    """A body list key, as a list.

    An empty or null key is a spec that holds none of that object rather
    than an error, the same way an empty `variables:` is.
    """
    if value is None:
        return []

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PhabfiveDataException(
            f"{key}: in {source} is a {type(value).__name__}, not a list"
        )
    return list(value)


def _folded(body: dict[str, Any], kind: Kind, *, source: str) -> dict[str, Any]:
    """One document's body, with its item keys in their normalized shape.

    For a search spec this is where the legacy flat shape becomes a
    `searches:` item: `{title, description, search}` at the top of a document
    is one search, and its `title`/`description` are that search's result
    banner - not the file's `metadata`.
    """
    folded = dict(body)

    for key in _ITEM_KEYS[kind]:
        if key in folded:
            folded[key] = _items(folded[key], key, source=source)

    if kind is not Kind.SEARCH:
        return folded

    legacy = [key for key in LEGACY_SEARCH_KEYS if key in folded]

    if "searches" in folded:
        if legacy:
            raise PhabfiveDataException(
                f"A document in {source} carries both searches: and the "
                f"top-level {', '.join(legacy)} of a single search. Write one "
                f"or the other, not both"
            )
        folded["searches"] = [
            _search_item(item, source=source) for item in folded["searches"]
        ]
        return folded

    if not legacy:
        if not folded:
            # A document carrying nothing but the envelope: `spec:`, `kind:`
            # and `metadata:` at the top of a multi-document file. There is
            # no search here, and inventing an empty one would run an
            # unconstrained search nobody asked for.
            return folded

        # A document with a body but none of title/description/search - one
        # holding only `variables:`, say. `Maniphest._load_search_config`
        # answers it with `{"search": {}, "title": None, "description":
        # None}` and runs it, so this folds to the same thing: the two
        # readers of a legacy multi-document template must not disagree
        # about how many searches it holds.
        folded["searches"] = [_search_item({}, source=source)]
        return folded

    item = {key: folded.pop(key) for key in legacy}
    folded["searches"] = [_search_item(item, source=source)]
    return folded


def _merged(bodies: Sequence[dict[str, Any]], kind: Kind) -> dict[str, Any]:
    """Every document's body, folded into one.

    Item keys concatenate in file order, which is the whole point: three
    documents each holding one search become one `searches:` of three.
    Mappings merge key by key, and anything else the later document wins -
    but `spec:`, `kind:` and `metadata:` never reach here, so the only way to
    hit that is a repeated scalar body key.
    """
    merged: dict[str, Any] = {}
    item_keys = _ITEM_KEYS[kind]

    for body in bodies:
        for key, value in body.items():
            if key in item_keys:
                merged.setdefault(key, []).extend(value)
            elif isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value

    return merged


def normalize_documents(
    documents: Iterable[Document],
    *,
    kind: KindLike | None = None,
    source: str = DATA_SOURCE,
) -> tuple[Envelope, dict[str, Any]]:
    """Fold a spec's documents into one envelope and one body.

    A multi-document file is one spec, not several: the envelope is declared
    once - by whichever document declares it, refusing two that disagree -
    and the item keys concatenate in file order.

    Parameters
    ----------
    documents : iterable of mappings
        The documents, in file order, as `phabfive.spec.loader` parses them.
        Not copied here; `Spec.from_data` is what deep-copies.
    kind : Kind or str, optional
        An explicit kind, which beats both a declared `kind:` and inference.
    source : str, optional
        What to call the spec in an error message.

    Returns
    -------
    (Envelope, dict)
        The envelope, and the body with the envelope keys removed and the
        item keys normalized. `variables:` is still in the body.
    """
    parsed = list(documents)

    spec_version, _ = _declared(parsed, "spec", source=source)
    declared_kind, kind_declared = _declared(parsed, "kind", source=source)
    metadata, _ = _declared(parsed, "metadata", source=source)

    if spec_version is not None and spec_version not in SUPPORTED_SPEC_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_SPEC_VERSIONS))
        raise PhabfiveDataException(
            f"{source} is written for spec version {spec_version!r}, which this "
            f"phabfive does not read. It reads: {supported}"
        )

    bodies = [
        {key: value for key, value in document.items() if key not in ENVELOPE_KEYS}
        for document in parsed
    ]

    if kind is not None:
        resolved = as_kind(kind, where="kind argument")
        if (
            kind_declared
            and as_kind(declared_kind, where=f"kind: in {source}") is not resolved
        ):
            log.debug(
                "%s declares kind: %s; the caller asked for %s",
                source,
                declared_kind,
                resolved.value,
            )
    elif kind_declared:
        resolved = as_kind(declared_kind, where=f"kind: in {source}")
    else:
        resolved = infer_kind(bodies, source=source)

    envelope = Envelope(
        spec=spec_version,
        kind=resolved,
        kind_declared=kind_declared,
        metadata=Metadata.from_data(metadata, source=source),
    )

    body = _merged(
        [_folded(body, resolved, source=source) for body in bodies], resolved
    )

    return envelope, body


def split_envelope(
    document: Document,
    *,
    kind: KindLike | None = None,
    source: str = DATA_SOURCE,
) -> tuple[Envelope, dict[str, Any]]:
    """Separate one document's envelope from its body.

    The single-document case of `normalize_documents`; see there.
    """
    return normalize_documents([document], kind=kind, source=source)


@dataclasses.dataclass(frozen=True)
class Spec:
    """One spec: an envelope, an uninterpreted body, and its variables.

    A `Spec` is what every later pass takes - offline validation, online
    validation, rendering, and eventually planning - and what a web frontend
    holds between reading a file and showing a report.

    Treat `body` as **read-only**. The dataclass is frozen and nothing in
    `phabfive.spec` mutates a body after construction, but the mappings
    inside it are plain dicts and Python will not stop you. `from_data`
    deep-copies what it is given, so the caller's data is never touched.

    `source` and `format` are provenance for error messages and deliberately
    do not take part in equality: the same spec written as YAML and as TOML
    is the same spec, which is the promise `phabfive.spec.loader` makes.

    Attributes
    ----------
    envelope : Envelope
        Format version, kind and metadata.
    body : mapping
        The document with the envelope keys and `variables:` removed, and its
        item keys normalized: `searches`, `tasks`, `projects`, `pastes`.
    variables : mapping
        The declared variables, unrendered - a value, or `{default: ...}`.
    source : str
        A path, or "<data>". For error messages only.
    format : str or None
        "yaml", "json", "jsonl", "toml", or None when built from data.
    rendered : bool
        Whether the variables have been rendered into the body.
    """

    envelope: Envelope
    body: Mapping[str, Any]
    variables: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    source: str = dataclasses.field(default=DATA_SOURCE, compare=False)
    format: str | None = dataclasses.field(default=None, compare=False)
    rendered: bool = False

    @property
    def kind(self) -> Kind:
        """Shorthand for `spec.envelope.kind`."""
        return self.envelope.kind

    @property
    def metadata(self) -> Metadata:
        """Shorthand for `spec.envelope.metadata`."""
        return self.envelope.metadata

    @classmethod
    def from_data(
        cls,
        data: Any,
        *,
        source: str = DATA_SOURCE,
        kind: KindLike | None = None,
        format: str | None = None,
    ) -> Spec:
        """Build a spec from parsed data.

        Parameters
        ----------
        data : mapping or list of mappings
            One document, or every document of a multi-document file in file
            order. Deep-copied, so the caller keeps its own object.
        source : str, optional
            What to call it in an error message. A path, or "<data>".
        kind : Kind or str, optional
            An explicit kind, which beats both a declared `kind:` and
            inference. This is what makes a caller that already knows what it
            holds - a legacy search template with no `search:` key, a web
            form - safe.
        format : str, optional
            The serialization it was written in, for error messages.

        Raises
        ------
        PhabfiveDataException
            The data is not a document or a list of them, the documents
            disagree about their envelope, `spec:` names an unknown version,
            or the kind can be neither supplied nor inferred.
        PhabfiveInputException
            `kind` - the argument or the file's - is not a kind.
        """
        documents = _documents(data, source=source)
        envelope, body = normalize_documents(documents, kind=kind, source=source)

        variables = body.pop(VARIABLES_KEY, None)

        # An absent, empty or null `variables:` is a spec that declares no
        # variables rather than an error, which is what the create templates
        # have always meant
        if variables is None:
            variables = {}
        elif not isinstance(variables, Mapping):
            raise PhabfiveDataException(
                f"variables: in {source} takes a mapping of name to value, not "
                f"{variables!r}"
            )
        else:
            variables = dict(variables)

        return cls(
            envelope=envelope,
            body=body,
            variables=variables,
            source=source,
            format=format,
        )

    def validate_offline(
        self, *, variables: Mapping[str, Any] | None = None
    ) -> list[Problem]:
        """Everything wrong with this spec that needs no server.

        A list of `phabfive.spec.problems.Problem`, empty when the spec is
        clean, and never an exception for a bad spec. See
        `phabfive.spec.validate.validate_offline`, which this is a shorthand
        for - the import is inside the method because that module imports
        `Spec` back, under `TYPE_CHECKING` only, and this is the end of the
        cycle that keeps both annotations honest.

        Parameters
        ----------
        variables : mapping, optional
            Values supplied from outside the spec, which is what ``--set``
            spells on the command line.
        """
        from phabfive.spec.validate import validate_offline

        return validate_offline(self, variables=variables)

    def render(self, variables: Mapping[str, Any] | None = None) -> Spec:
        """A new spec with every `{{ name }}` in its body replaced.

        The variables are the spec's own `variables:` section with
        `variables` layered on top, which is what ``--set name=value``
        spells on the command line; an override beats a declared default,
        and an override for a name the spec never declared is kept, so a
        spec may use `{{ sprint }}` without declaring it as long as every
        caller supplies one.

        The result carries the *resolved* variables rather than the
        declarations, and `rendered` is True. This spec is unchanged - as
        everywhere else here, a new object is returned rather than the body
        mutated.

        Parameters
        ----------
        variables : mapping, optional
            Values supplied from outside the spec.

        Returns
        -------
        Spec
            The rendered spec.

        Raises
        ------
        PhabfiveDataException
            A name nothing supplies, a declared variable with neither a
            value nor a default, or a cycle between them. This raises rather
            than reporting because a half-rendered spec is not a spec;
            `validate_offline` is what reports the same mistakes as problems
            a person can read all at once.
        """
        from phabfive.spec.variables import render_tree, resolve_variables

        values = resolve_variables(self.variables, variables)

        return dataclasses.replace(
            self,
            body=render_tree(dict(self.body), values),
            variables=values,
            rendered=True,
        )

    def validate_online(self, app: Any) -> list[Problem]:
        """Everything wrong with this spec that only the instance can answer.

        A list of `phabfive.spec.problems.Problem`, empty when every
        reference resolves. See `phabfive.spec.online.validate_online`,
        which this is a shorthand for - the import is inside the method for
        the same reason `validate_offline`'s is.

        Parameters
        ----------
        app : Phabfive
            An already-constructed app, e.g. `Maniphest(url=..., token=...)`.
            Nothing here constructs one or reads a configuration.

        Raises
        ------
        PhabfiveRemoteException
            The instance could not be asked. It propagates rather than
            becoming a problem: a clean report for a check that never ran is
            worse than an error.
        """
        from phabfive.spec.online import validate_online

        return validate_online(self, app)

    def items(self, object_type: str) -> list[Any]:
        """The items of one object type, empty when the spec has none.

        `spec.items("task")` and `spec.items("tasks")` are the same thing, so
        a caller never has to remember whether the key is plural. A new list
        every call; the items themselves are the spec's own mappings and are
        read-only like the rest of the body.

        Raises
        ------
        PhabfiveInputException
            `object_type` is not an object type a spec can hold. That is a
            programming error, not something a spec file can cause.
        """
        key = _BODY_KEY_BY_OBJECT.get(object_type)

        if key is None:
            known = ", ".join(sorted(set(_BODY_KEY_BY_OBJECT.values())))
            raise PhabfiveInputException(
                f"Unknown spec object type {object_type!r}. A spec holds: {known}"
            )

        return list(self.body.get(key) or [])

    def to_data(self) -> dict[str, Any]:
        """The spec as one plain document, which parses back to itself.

        `kind:` is always written, including when it was inferred: the point
        of the round trip is a file nothing has to guess about. `spec:` is
        written only when the original carried it, so a file with no envelope
        does not grow one.
        """
        data: dict[str, Any] = {}

        if self.envelope.spec is not None:
            data["spec"] = self.envelope.spec

        data["kind"] = self.envelope.kind.value

        metadata = self.envelope.metadata.as_data()
        if metadata:
            data["metadata"] = metadata

        if self.variables:
            data[VARIABLES_KEY] = copy.deepcopy(dict(self.variables))

        data.update(copy.deepcopy(dict(self.body)))

        return data


__all__ = [
    "DATA_SOURCE",
    "DEFAULT_SEARCH_TYPE",
    "ENVELOPE_KEYS",
    "LEGACY_SEARCH_KEYS",
    "METADATA_KEYS",
    "SPEC_VERSION",
    "SUPPORTED_SPEC_VERSIONS",
    "VARIABLES_KEY",
    "Envelope",
    "Kind",
    "Metadata",
    "Spec",
    "as_kind",
    "infer_kind",
    "normalize_documents",
    "split_envelope",
]
