# -*- coding: utf-8 -*-
"""One loader, four serializations, one internal shape.

A spec may be written as YAML, JSON, JSONL or TOML. Whichever it is, it
parses to the same thing: a list of plain Python mappings, in file order.

| Format | Single document | Several documents        |
|--------|-----------------|--------------------------|
| YAML   | yes             | `---` separators         |
| JSON   | yes             | a list at the root       |
| JSONL  | n/a             | one document per line    |
| TOML   | yes             | list keys only           |

Several documents in one file is a serialization convenience, not a second
shape: `phabfive.spec.envelope.normalize_documents` folds them into one
document whose list keys (`searches:`, `tasks:`, `projects:`, `pastes:`) hold
the same items, and those keys are what every format can express. TOML says
it with `[[searches]]`, JSON with an array, YAML either way.

Three limitations are documented rather than worked around:

- YAML anchors (`&WORKGROUP`/`*WORKGROUP`) are a YAML feature and resolve
  only there. `variables:` is the cross-format way to reuse a value.
- JSON and JSONL have no comments, which is why `metadata.description`
  matters more there.
- TOML has no null; an explicitly absent value is expressed by omitting the
  key.

Everything comes in pairs - `load_spec(path)` beside `parse_spec(text)`,
`load_documents(path)` beside `parse_documents(text)` - because a web
frontend receives a spec in a request body and must never be forced to write
a temp file first.

ruamel.yaml is kept for YAML rather than routing everything through
anyconfig: `load_all` and `preserve_quotes` come from it, and
`specs/create/feature-epic.yaml` depends on anchors resolving.
JSON and TOML get thin readers over the standard library instead of a hidden
backend registry.

`load_spec` and `parse_spec` answer with a `phabfive.spec.envelope.Spec`:
the documents folded into one envelope and one body. `load_documents` and
`parse_documents` are the layer underneath, for a caller that wants the
documents as they were written.

This module is library code: it does not print, prompt, exit, read the
environment or read configuration, and it imports nothing from phabfive but
`phabfive.exceptions` and `phabfive.spec.envelope`. The dependency runs
loader -> envelope and never the other way.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.spec.envelope import DATA_SOURCE as _DATA_SOURCE
from phabfive.spec.envelope import Kind, Spec, as_kind

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 has no tomllib; tomli is the same parser
    import tomli as tomllib

log = logging.getLogger(__name__)


class SpecFormat(enum.Enum):
    """The serializations a spec may be written in.

    `.value` is what a `Spec` carries and what JSON output names.
    """

    YAML = "yaml"
    JSON = "json"
    JSONL = "jsonl"
    TOML = "toml"


# Spellings accepted wherever a format is named, beyond the enum values
# themselves. "ndjson" is the same alias phabfive's output formats use.
_FORMAT_ALIASES = {
    "yml": SpecFormat.YAML,
    "ndjson": SpecFormat.JSONL,
}

# Suffixes, without the dot and lowercased.
_EXTENSIONS = {
    "yaml": SpecFormat.YAML,
    "yml": SpecFormat.YAML,
    "json": SpecFormat.JSON,
    "jsonl": SpecFormat.JSONL,
    "ndjson": SpecFormat.JSONL,
    "toml": SpecFormat.TOML,
}

# "Invalid value (at line 1, column 9)", as tomli writes it before 2.2
_TOML_POSITION = re.compile(r"\s*\(at line (\d+), column (\d+)\)\s*$")


def _format_names() -> str:
    """The formats a message offers, in a stable order."""
    return ", ".join(member.value for member in SpecFormat)


def _coerce_format(name: SpecFormat | str) -> SpecFormat:
    """A SpecFormat, or the name of one, as a SpecFormat."""
    if isinstance(name, SpecFormat):
        return name

    if isinstance(name, str):
        key = name.strip().lower().lstrip(".")
        if key in _FORMAT_ALIASES:
            return _FORMAT_ALIASES[key]
        for member in SpecFormat:
            if member.value == key:
                return member

    raise PhabfiveInputException(
        f"Unknown spec format {name!r}. Known formats: {_format_names()}"
    )


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _is_toml(text: str) -> bool:
    try:
        tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return False
    return True


def _sniff(text: str) -> SpecFormat:
    """Guess a format from the text itself.

    Only reached when neither an explicit format nor a known suffix said so.
    YAML is the fallback because it is the permissive one: whatever the text
    turns out to be, its parse error is the most readable of the four.
    """
    lines = [line for line in (raw.strip() for raw in text.splitlines()) if line]

    if len(lines) > 1 and all(_is_json(line) for line in lines):
        return SpecFormat.JSONL
    if _is_json(text):
        return SpecFormat.JSON
    if _is_toml(text):
        return SpecFormat.TOML
    return SpecFormat.YAML


def detect_format(
    path: str | os.PathLike[str] | None = None,
    *,
    text: str | None = None,
    format: SpecFormat | str | None = None,
) -> SpecFormat:
    """Decide which serialization a spec is written in.

    An explicit `format` wins; then the file suffix; then the content. Pass
    `text` as well as `path` so that a file with an unhelpful name - `-`, a
    temp file, `spec.txt` - is still sniffed rather than refused.

    Raises
    ------
    PhabfiveInputException
        When `format` names something that is not a format, or when there is
        neither a usable suffix nor any text to look at.
    """
    if format is not None:
        return _coerce_format(format)

    if path is not None:
        suffix = Path(os.fspath(path)).suffix.lower().lstrip(".")
        known = _EXTENSIONS.get(suffix)
        if known is not None:
            return known

    if text is not None:
        return _sniff(text)

    where = f" of {os.fspath(path)!r}" if path is not None else ""
    raise PhabfiveInputException(
        f"Cannot tell the format{where}: name it with format=, or give it a "
        f"suffix. Known formats: {_format_names()}"
    )


def _parse_failure(
    source: str,
    spec_format: SpecFormat,
    detail: str,
    line: int | None = None,
    column: int | None = None,
) -> PhabfiveDataException:
    """One sentence naming the file, the format and the position.

    Every parser reports a position differently - ruamel hands over a
    `problem_mark`, json a `lineno`/`colno`, tomli a message with the
    position inside it - so they are normalized here and nowhere else.
    """
    where = ""
    if line is not None:
        where = f" at line {line}"
        if column is not None:
            where += f", column {column}"

    return PhabfiveDataException(
        f"Failed to parse {source} as {spec_format.value}{where}: {detail}"
    )


def _parse_yaml(text: str, source: str) -> list[Any]:
    yaml = YAML()
    yaml.preserve_quotes = True

    try:
        # load_all is lazy, so a syntax error in the third document only
        # surfaces once the list is built
        return list(yaml.load_all(text))
    except MarkedYAMLError as error:
        mark = error.problem_mark
        detail = (error.problem or str(error)).strip()
        raise _parse_failure(
            source,
            SpecFormat.YAML,
            detail,
            line=None if mark is None else mark.line + 1,
            column=None if mark is None else mark.column + 1,
        ) from error
    except YAMLError as error:
        # A YAML error ruamel reported without a position - a duplicate key,
        # an unknown tag, an alias to an anchor that does not exist. Narrow
        # on purpose: a blanket `except Exception` here would report a bug in
        # ruamel, or a RecursionError on a deeply nested document, as "Failed
        # to parse <file> as yaml", which sends a person looking for a syntax
        # error that is not there.
        raise _parse_failure(source, SpecFormat.YAML, str(error).strip()) from error


def _parse_json(text: str, source: str) -> list[Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise _parse_failure(
            source, SpecFormat.JSON, error.msg, line=error.lineno, column=error.colno
        ) from error

    # A list at the root is JSON's spelling of a multi-document file, the
    # same thing YAML writes with "---"
    return list(data) if isinstance(data, list) else [data]


def _parse_jsonl(text: str, source: str) -> list[Any]:
    documents: list[Any] = []

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            documents.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise _parse_failure(
                source,
                SpecFormat.JSONL,
                error.msg,
                line=number,
                column=error.colno,
            ) from error

    return documents


def _parse_toml(text: str, source: str) -> list[Any]:
    try:
        # tomllib.loads always answers with a mapping, so TOML has no
        # multi-document form beyond its list keys
        return [tomllib.loads(text)]
    except tomllib.TOMLDecodeError as error:
        # 3.14 and tomli 2.2 carry lineno/colno/msg; older tomli only puts
        # the position inside the message, which _TOML_POSITION peels off so
        # that one sentence does not name the position twice
        line = getattr(error, "lineno", None)
        column = getattr(error, "colno", None)
        detail = getattr(error, "msg", None) or str(error)
        found = _TOML_POSITION.search(detail)
        if found is not None:
            detail = detail[: found.start()]
            if line is None:
                line, column = int(found.group(1)), int(found.group(2))
        raise _parse_failure(
            source, SpecFormat.TOML, detail.strip(), line=line, column=column
        ) from error


_PARSERS = {
    SpecFormat.YAML: _parse_yaml,
    SpecFormat.JSON: _parse_json,
    SpecFormat.JSONL: _parse_jsonl,
    SpecFormat.TOML: _parse_toml,
}


def _plain(value: Any) -> Any:
    """Plain dicts, lists and strings, whichever parser produced the value.

    ruamel answers with CommentedMap, CommentedSeq and the quote-preserving
    string subclasses; json and tomllib answer with builtins. Levelling them
    here is what makes "the same spec in four formats parses to an identical
    structure" true of the objects and not only of their contents.

    Containers and text only. A scalar comes back as whatever its parser
    read it as, which is deliberate: a TOML or YAML date is a `date`, not a
    string, and coercing it here would decide what a date means to a spec
    before any field has said it takes one. So the result is *not*
    unconditionally `json.dumps`-able - a spec carrying a bare date needs
    `default=str`, the same way `Problem.value` does. Mapping keys are
    coerced with `str`, because a spec key is a name: YAML reads `1: x` as
    an integer key and TOML has no non-string keys at all.
    """
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, str):
        return str(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_plain(item) for item in value]
    return value


def parse_documents(
    text: str,
    *,
    format: SpecFormat | str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Parse spec text into its documents, in file order.

    Parameters
    ----------
    text : str
        The whole spec, as text.
    format : SpecFormat or str, optional
        Which serialization it is. Sniffed from the text when omitted.
    source : str, optional
        What to call it in an error message. Defaults to "<data>".

    Returns
    -------
    list of dict
        One plain mapping per document. Never empty - a file with no
        document in it is a parse failure, not an empty spec.

    Raises
    ------
    PhabfiveInputException
        `format` is not a format.
    PhabfiveDataException
        The text does not parse, holds no document, or holds a document that
        is not a mapping.
    """
    where = _DATA_SOURCE if source is None else source

    # Detecting a format for whitespace is guesswork, and every parser
    # answers an empty file differently - tomllib says {}, json raises,
    # ruamel yields nothing - so say it once, here
    if not text.strip():
        raise PhabfiveDataException(f"{where} holds no spec document: it is empty")

    spec_format = detect_format(text=text, format=format)

    documents = _PARSERS[spec_format](text, where)

    # A trailing "---", or a blank JSONL line, is an empty document and not
    # an error. A file with nothing else in it is.
    documents = [document for document in documents if document is not None]

    if not documents:
        raise PhabfiveDataException(f"{where} holds no {spec_format.value} document")

    for index, document in enumerate(documents):
        if not isinstance(document, Mapping):
            position = "" if len(documents) == 1 else f" {index + 1}"
            raise PhabfiveDataException(
                f"Document{position} in {where} ({spec_format.value}) is a "
                f"{type(document).__name__} at the root level, not a mapping"
            )

    log.debug(
        "Parsed %d %s document(s) from %s", len(documents), spec_format.value, where
    )

    return [_plain(document) for document in documents]


def _read(path: str | os.PathLike[str]) -> tuple[Path, str]:
    """A spec file's path and its text, or the reason neither can be had.

    Shared by `load_documents` and `load_spec` so that the two answer a
    missing file, a directory and a non-UTF-8 file with exactly the same
    exception, and so that neither reads the file twice to name its format.
    """
    spec_file = Path(os.fspath(path))

    if not spec_file.exists():
        raise PhabfiveConfigException(f"Spec file not found: {spec_file}")
    if not spec_file.is_file():
        raise PhabfiveConfigException(f"Path is not a file: {spec_file}")

    try:
        text = spec_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise PhabfiveDataException(
            f"Spec file {spec_file} is not UTF-8 text: {error}"
        ) from error
    except OSError as error:
        raise PhabfiveConfigException(
            f"Could not read spec file {spec_file}: {error}"
        ) from error

    return spec_file, text


def load_documents(
    path: str | os.PathLike[str],
    *,
    format: SpecFormat | str | None = None,
) -> list[dict[str, Any]]:
    """Read a spec file and parse it into its documents, in file order.

    The path's suffix picks the format; an unknown suffix falls back to
    sniffing the content, and `format=` overrides both.

    Raises
    ------
    PhabfiveConfigException
        The path does not exist, is not a file, or cannot be read.
    PhabfiveDataException
        The file does not parse; see `parse_documents`.
    """
    spec_file, text = _read(path)

    return parse_documents(
        text,
        format=detect_format(spec_file, text=text, format=format),
        source=str(spec_file),
    )


def _checked_kind(kind: Any) -> Kind | None:
    """The kind a caller supplied, as a Kind, or None.

    `phabfive.spec.envelope` owns what a kind is, so an unknown one is
    refused there and in the same words wherever it was named - an argument,
    or a `kind:` in the file.
    """
    if kind is None:
        return None

    return as_kind(kind, where="kind")


def parse_spec(
    text: str,
    *,
    format: SpecFormat | str | None = None,
    source: str | None = None,
    kind: Any = None,
) -> Spec:
    """Parse spec text into a `Spec`, for a caller that holds it as text.

    The pair of `load_spec`, for a web frontend with a request body rather
    than a file.

    `kind` overrides inference: "create" or "search". A search template may
    legally carry only `title:` and `description:`, which inference cannot
    tell apart from a create spec, so the legacy call sites say which they
    are instead of guessing.

    The documents are folded into one `Spec` - a multi-document file is one
    spec, not several; see `phabfive.spec.envelope.normalize_documents`.
    """
    checked = _checked_kind(kind)
    where = _DATA_SOURCE if source is None else source
    documents = parse_documents(text, format=format, source=source)

    return Spec.from_data(
        documents,
        source=where,
        kind=checked,
        format=detect_format(text=text, format=format).value,
    )


def load_spec(
    path: str | os.PathLike[str],
    *,
    format: SpecFormat | str | None = None,
    kind: Any = None,
) -> Spec:
    """Read a spec file and parse it into a `Spec`.

    The pair of `parse_spec`; see there.

    Raises
    ------
    PhabfiveConfigException
        The path does not exist, is not a file, or cannot be read.
    PhabfiveDataException
        The file does not parse, or does not hold one coherent spec.
    """
    _checked_kind(kind)
    spec_file, text = _read(path)

    return parse_spec(
        text,
        format=detect_format(spec_file, text=text, format=format),
        source=str(spec_file),
        kind=kind,
    )


__all__ = [
    "SpecFormat",
    "detect_format",
    "load_documents",
    "load_spec",
    "parse_documents",
    "parse_spec",
]
