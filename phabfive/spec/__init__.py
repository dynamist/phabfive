# -*- coding: utf-8 -*-
"""The Phorge spec format: load it, validate it, and hand it to an app.

This is library code. Nothing under `phabfive/spec/` prints, prompts, exits,
reads the environment or reads a configuration file, and nothing here
imports `phabfive.cli` - importing that sets `TYPER_USE_RICH` in the process
environment, which touching a library attribute must never do.

A program that never sees the command line uses exactly this:

    from phabfive import Maniphest, Spec

    spec = Spec.from_data(request.json)          # a dict, not a file
    problems = spec.validate_offline()           # no network, no token
    app = Maniphest(url=URL, token=TOKEN)        # explicit; discovers nothing
    problems += spec.validate_online(app)        # every failure at once

Three thin methods sit on `Spec` - `render`, `validate_offline` and
`validate_online` - and each is the module-level function of the same name
with the spec already bound. `spec.render(variables={...})` is what `--set`
spells on the command line, and it is what a caller does before the online
pass: a value still holding `{{ who }}` names nothing an instance could be
asked about.

`Spec` and `Problem` are the two names `phabfive` itself re-exports, because
they are what a caller holds. Everything else lives here, one import deeper -
the tier `phabfive.transitions`, `phabfive.policy` and `phabfive.ordering`
already occupy.

The names below are this subpackage's promise. Its modules carry more that is
public still: `SpecFormat`, `detect_format`, `load_documents` and
`parse_documents` in `phabfive.spec.loader`; `SPEC_VERSION`, `infer_kind`,
`split_envelope` and `normalize_documents` in `phabfive.spec.envelope`;
`problem` and `Layer` in `phabfive.spec.problems`; `RefKind`,
`classify_reference` and `iter_references` in `phabfive.spec.references`;
`property_schema`, `TIME_PATTERN`, `SEARCH_ITEM_KEYS` and the grammar
helpers - `transition_pattern`, `policy_pattern`, `monogram_pattern`,
`monogram_list_pattern` - in `phabfive.spec.schema`; `Resolver`,
`ResolveResult`, `ReferenceIndex`, `index_references`, `field_kind` and
`DEFAULT_RESOLVERS` in `phabfive.spec.online`; `parse_time_with_unit` in
`phabfive.spec.times`; and the engine - `resolve_variables`, `render_tree`,
`render_string` - in `phabfive.spec.variables`. Each module's own `__all__`
is the full list.
"""

from phabfive.spec.envelope import Envelope as Envelope
from phabfive.spec.envelope import Kind as Kind
from phabfive.spec.envelope import Metadata as Metadata
from phabfive.spec.envelope import Spec as Spec
from phabfive.spec.loader import load_spec as load_spec
from phabfive.spec.loader import parse_spec as parse_spec
from phabfive.spec.online import validate_online as validate_online
from phabfive.spec.problems import Problem as Problem
from phabfive.spec.problems import Severity as Severity
from phabfive.spec.registry import Field as Field
from phabfive.spec.registry import FieldKind as FieldKind
from phabfive.spec.registry import spec_keys as spec_keys
from phabfive.spec.schema import build_schema as build_schema
from phabfive.spec.validate import validate_offline as validate_offline

# Both spellings are load-bearing: __all__ is what mypy's no_implicit_reexport
# reads, `import x as x` is what keeps ruff quiet about an unused import.
__all__ = [
    "Envelope",
    "Field",
    "FieldKind",
    "Kind",
    "Metadata",
    "Problem",
    "Severity",
    "Spec",
    "build_schema",
    "load_spec",
    "parse_spec",
    "spec_keys",
    "validate_offline",
    "validate_online",
]
