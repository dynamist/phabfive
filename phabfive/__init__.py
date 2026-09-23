# -*- coding: utf-8 -*-
"""phabfive - a CLI for Phabricator and Phorge, and a library under it.

The command is one consumer of the classes it is built from:

    from phabfive import Maniphest

A program that reads a spec - a web frontend building one from a request
body, say - needs no command line either:

    from phabfive import Maniphest, Spec

    spec = Spec.from_data(payload)
    problems = spec.validate_offline()
    problems += spec.validate_online(Maniphest(url=URL, token=TOKEN))

`Spec` and `Problem` are the two spec names exported here, because they are
what a caller holds; `load_spec`, `validate_offline`, `validate_online` and
the rest are one import deeper, in `phabfive.spec`.

Every public name is resolved lazily (PEP 562), so `import phabfive` pulls in
no third-party module. The module that defines a name is imported the first
time the name is touched. See AGENTS.md for why this must stay lazy -- it is
not an optimisation that can be simplified away.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Declared for type checkers only; resolved at runtime by __getattr__.
    # Every name here must also appear in _LAZY and in __all__: mypy's
    # no_implicit_reexport refuses to re-export a name that is missing from
    # __all__, and a name missing from _LAZY type checks perfectly while
    # raising AttributeError at runtime. tests/test_public_api.py asserts all
    # three agree.
    __version__: str

    from phabfive.core import Phabfive
    from phabfive.diffusion import Diffusion
    from phabfive.edit import Edit, EditFailure, EditPlan, TaskEdit
    from phabfive.exceptions import (
        PhabfiveAPIException,
        PhabfiveConfigException,
        PhabfiveConnectionException,
        PhabfiveDataException,
        PhabfiveException,
        PhabfiveInputException,
        PhabfiveNameCollisionException,
        PhabfiveNotFoundException,
        PhabfiveRemoteException,
        PhabfiveValidationException,
    )
    from phabfive.maniphest import Maniphest
    from phabfive.passphrase import Passphrase
    from phabfive.paste import Paste
    from phabfive.project import Project
    from phabfive.spec import Problem, SearchPlan, SearchResult, Spec
    from phabfive.user import User

# The promise. Spelled out as literals rather than derived from _LAZY: ruff
# cannot see a computed __all__, and would flag every import above as unused.
__all__ = [
    "Diffusion",
    "Edit",
    "EditFailure",
    "EditPlan",
    "Maniphest",
    "Passphrase",
    "Paste",
    "Phabfive",
    "PhabfiveAPIException",
    "PhabfiveConfigException",
    "PhabfiveConnectionException",
    "PhabfiveDataException",
    "PhabfiveException",
    "PhabfiveInputException",
    "PhabfiveNameCollisionException",
    "PhabfiveNotFoundException",
    "PhabfiveRemoteException",
    "PhabfiveValidationException",
    "Problem",
    "Project",
    "SearchPlan",
    "SearchResult",
    "Spec",
    "TaskEdit",
    "User",
    "__version__",
]

# Public name -> the module that defines it, and the single source of truth
# for what __getattr__ will resolve. The app classes map to their subpackage
# rather than its core module, because the subpackage is where they are
# re-exported from.
_LAZY = {
    "Diffusion": "phabfive.diffusion",
    "Edit": "phabfive.edit",
    "EditFailure": "phabfive.edit",
    "EditPlan": "phabfive.edit",
    "Maniphest": "phabfive.maniphest",
    "Passphrase": "phabfive.passphrase",
    "Paste": "phabfive.paste",
    "Phabfive": "phabfive.core",
    "PhabfiveAPIException": "phabfive.exceptions",
    "PhabfiveConfigException": "phabfive.exceptions",
    "PhabfiveConnectionException": "phabfive.exceptions",
    "PhabfiveDataException": "phabfive.exceptions",
    "PhabfiveException": "phabfive.exceptions",
    "PhabfiveInputException": "phabfive.exceptions",
    "PhabfiveNameCollisionException": "phabfive.exceptions",
    "PhabfiveNotFoundException": "phabfive.exceptions",
    "PhabfiveRemoteException": "phabfive.exceptions",
    "PhabfiveValidationException": "phabfive.exceptions",
    "Problem": "phabfive.spec",
    "Project": "phabfive.project",
    "SearchPlan": "phabfive.spec",
    "SearchResult": "phabfive.spec",
    "Spec": "phabfive.spec",
    "TaskEdit": "phabfive.edit",
    "User": "phabfive.user",
}

# Resolved as attributes so `import phabfive; phabfive.maniphest.Maniphest`
# works. Without this it depends on whether something else already imported
# the submodule, which is worse than either answer consistently.
#
# Deliberately absent, because none of them is library code:
#   cli      sets os.environ["TYPER_USE_RICH"] as it imports, and a library
#            must never cause a process-global side effect because an
#            attribute was touched
#   repl     imports readline
#   setup    is an interactive wizard
#   display, record_display, table, json_output
#            print, and sys.exit on a closed pipe
#   cache    exists for shell completion
# `from phabfive import cache` still works: the import system falls back to
# importing the submodule when __getattr__ raises AttributeError.
_SUBMODULES = (
    "constants",
    "core",
    "diffusion",
    "edit",
    "exceptions",
    "fulltext",
    "maniphest",
    "options",
    "ordering",
    "pagination",
    "passphrase",
    "paste",
    "policy",
    "project",
    "search",
    "spec",
    "transitions",
    "user",
    "yaml_utils",
)


def _read_version() -> str:
    """Return the installed distribution's version, read on first access.

    Deferred rather than read at import: importlib.metadata parses the
    installed distributions' metadata, which is a cost a consumer who never
    asks for the version should not pay.

    A source tree that was never installed has no metadata at all, so this
    falls back instead of raising -- a library that cannot be imported from a
    checkout is a library nobody can vendor. scripts/smoke.py rejects the
    fallback string, so a release artifact that lost its metadata still fails.

    The call keeps the literal "phabfive": PyInstaller finds the metadata a
    frozen build needs by scanning for exactly that.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("phabfive")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def __getattr__(name: str) -> object:
    """Resolve a public name on first access, then cache it (PEP 562).

    Annotated `-> object` rather than `Any` on purpose: `Any` would make a
    consumer's typo silently well-typed, while `object` fails at the point of
    use. The names in the TYPE_CHECKING block keep their real types either way.
    """
    if name in _SUBMODULES:
        return import_module(f"phabfive.{name}")
    if name == "__version__":
        value: object = _read_version()
    else:
        module = _LAZY.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(import_module(module), name)
    # Bound in the module namespace, so __getattr__ runs once per name.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Offer the curated surface to dir() and REPL completion."""
    return sorted({*__all__, *_SUBMODULES})
