"""The top-level namespace is a promise, and it is resolved lazily.

`phabfive/__init__.py` re-exports its public surface through a PEP 562
`__getattr__` rather than importing the modules eagerly, for two reasons that
no other test in this suite can observe:

* `import phabfive` must not drag in phabricator, requests, anyconfig or rich.
  The phabricator package reads ~/.arcrc and ./.arcconfig as it imports, so
  eagerness here is not only slow but reads the user's files.
* `phabfive/cli/__init__.py` sets `os.environ["TYPER_USE_RICH"]` as it
  imports. That is a process-global side effect, and merely touching an
  attribute on `phabfive` must never cause it.

Both are properties of a *fresh* interpreter. By the time any in-process
assertion runs, the rest of this suite has already imported everything, so
the laziness checks shell out.

The other half of the file guards a failure a type checker structurally
cannot see: the TYPE_CHECKING block, `__all__` and `_LAZY` are three separate
lists of the same names, and a name present in the first two but missing from
the third type checks perfectly while raising AttributeError at runtime.
"""

import ast
import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import phabfive

# Nothing here may be in sys.modules after a bare `import phabfive`.
BANNED = [
    "phabricator",  # reads ~/.arcrc and ./.arcconfig as it imports
    "requests",
    "anyconfig",
    "appdirs",
    "jinja2",
    "ruamel.yaml",
    "yaml",
    "rich",
    "typer",  # CLI only
    "click",  # CLI only
    "InquirerPy",  # CLI only
    "readline",  # repl only, and absent on Windows
    "importlib.metadata",  # __version__ is lazy too
    "logging.config",  # init_logging is the CLI's
    "phabfive.cli",
    "phabfive.core",
    "phabfive.maniphest",
    "phabfive.diffusion",
    "phabfive.edit",
    "phabfive.passphrase",
    "phabfive.paste",
    "phabfive.project",
    "phabfive.user",
]

# The CLI and the terminal. None of these may load because a library name was
# touched.
CLI_ONLY = ["typer", "click", "InquirerPy", "phabfive.cli"]

_PUBLIC = sorted(set(phabfive.__all__) - {"__version__"})


def _run(code):
    """Run `code` in a fresh interpreter, with TYPER_USE_RICH unset.

    Stripping the variable is what makes the side-effect assertion mean
    something: if the parent environment already set it, importing
    phabfive.cli would be undetectable.
    """
    env = {k: v for k, v in os.environ.items() if k != "TYPER_USE_RICH"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _loaded_after(code, names):
    """The subset of `names` in sys.modules after running `code` fresh."""
    listed = ", ".join(repr(name) for name in names)
    return _run(
        f"import sys, json; {code}; "
        f"print(json.dumps([n for n in [{listed}] if n in sys.modules]))"
    )


class TestResolution:
    @pytest.mark.parametrize("name", _PUBLIC)
    def test_name_resolves_to_its_modules_object(self, name):
        """Identity, not equality: a future wrapper or copy must fail here."""
        module = importlib.import_module(phabfive._LAZY[name])
        assert getattr(phabfive, name) is getattr(module, name)

    @pytest.mark.parametrize("name", _PUBLIC)
    def test_name_is_public_in_its_own_module(self, name):
        """A top-level export must still be public where it is defined."""
        module = importlib.import_module(phabfive._LAZY[name])
        assert name in module.__all__

    @pytest.mark.parametrize("name", phabfive._SUBMODULES)
    def test_submodule_resolves(self, name):
        assert getattr(phabfive, name) is importlib.import_module(f"phabfive.{name}")

    def test_repeated_access_is_the_same_object(self):
        """__getattr__ caches into globals(); it must not rebuild each time."""
        assert phabfive.Maniphest is phabfive.Maniphest

    def test_app_classes_share_the_base(self):
        for name in (
            "Diffusion",
            "Edit",
            "Maniphest",
            "Passphrase",
            "Paste",
            "Project",
            "User",
        ):
            assert issubclass(getattr(phabfive, name), phabfive.Phabfive)

    def test_exceptions_share_the_base(self):
        for name in _PUBLIC:
            if name.endswith("Exception"):
                assert issubclass(getattr(phabfive, name), phabfive.PhabfiveException)

    def test_unlisted_submodule_still_imports(self):
        """`from phabfive import cache` falls back to a submodule import."""
        from phabfive import cache

        assert cache is importlib.import_module("phabfive.cache")


class TestSurfacesAgree:
    """__all__, _LAZY and the TYPE_CHECKING block are three copies of one list."""

    def test_all_matches_lazy(self):
        assert set(phabfive.__all__) == set(phabfive._LAZY) | {"__version__"}

    def test_type_checking_block_matches_lazy(self):
        """Parse the source: a name missing from _LAZY still type checks."""
        tree = ast.parse(Path(phabfive.__file__).read_text(encoding="utf-8"))
        imported = {}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.If)
                and getattr(node.test, "id", "") == "TYPE_CHECKING"
            ):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    for alias in child.names:
                        imported[alias.name] = child.module
        assert imported == phabfive._LAZY

    def test_all_is_sorted_and_unique(self):
        assert phabfive.__all__ == sorted(phabfive.__all__)
        assert len(phabfive.__all__) == len(set(phabfive.__all__))

    @pytest.mark.parametrize(
        "name",
        [
            "cli",
            "repl",
            "setup",
            "display",
            "record_display",
            "table",
            "json_output",
            "cache",
        ],
    )
    def test_non_library_module_is_not_offered(self, name):
        """Touching an attribute must never import the CLI or the terminal."""
        assert name not in phabfive._SUBMODULES
        assert not any(m == f"phabfive.{name}" for m in phabfive._LAZY.values())


class TestAttributeProtocol:
    def test_unknown_attribute_raises_attribute_error(self):
        match = r"module 'phabfive' has no attribute 'no_such_name'"
        with pytest.raises(AttributeError, match=match):
            _ = phabfive.no_such_name

    def test_hasattr_is_false_for_unknown(self):
        """Proves __getattr__ raises AttributeError and not KeyError."""
        assert not hasattr(phabfive, "no_such_name")

    def test_dir_offers_the_surface(self):
        assert set(phabfive.__all__) <= set(dir(phabfive))
        assert set(phabfive._SUBMODULES) <= set(dir(phabfive))

    def test_dir_is_sorted(self):
        assert dir(phabfive) == sorted(dir(phabfive))


class TestVersion:
    def test_matches_the_distribution_metadata(self):
        assert phabfive.__version__ == importlib.metadata.version("phabfive")

    def test_is_a_string(self):
        assert isinstance(phabfive.__version__, str)

    def test_falls_back_when_metadata_is_missing(self, monkeypatch):
        """A vendored or never-installed tree must still import.

        scripts/smoke.py rejects this exact string, so a release artifact that
        lost its metadata is still caught.
        """

        def raise_not_found(_name):
            raise importlib.metadata.PackageNotFoundError(_name)

        monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
        assert phabfive._read_version() == "0.0.0+unknown"


class TestLaziness:
    def test_bare_import_pulls_in_nothing(self):
        assert _loaded_after("import phabfive", BANNED) == "[]"

    def test_bare_import_has_no_cli_side_effect(self):
        out = _run(
            "import os; import phabfive; print(os.environ.get('TYPER_USE_RICH'))"
        )
        assert out == "None"

    def test_every_public_name_resolves_in_a_fresh_interpreter(self):
        """Catches a _LAZY entry naming a module or attribute that is gone."""
        out = _run(
            "import phabfive; [getattr(phabfive, n) for n in phabfive.__all__]; "
            "print('ok')"
        )
        assert out == "ok"

    def test_every_public_name_stays_out_of_the_cli(self):
        code = "import phabfive; [getattr(phabfive, n) for n in phabfive.__all__]"
        assert _loaded_after(code, CLI_ONLY) == "[]"

    def test_every_public_name_stays_off_rich(self):
        """Records hold plain strings, so the library has no use for rich."""
        code = "import phabfive; [getattr(phabfive, n) for n in phabfive.__all__]"
        assert _loaded_after(code, ["rich"]) == "[]"

    def test_touching_names_leaves_the_environment_alone(self):
        out = _run(
            "import os; before = dict(os.environ); import phabfive; "
            "[getattr(phabfive, n) for n in phabfive.__all__]; "
            "print(before == dict(os.environ))"
        )
        assert out == "True"

    def test_constructing_an_app_stays_a_library(self, tmp_path):
        """Build a Maniphest the way a program would, in a bare interpreter.

        An empty HOME and cwd leave nothing to discover, the discard port
        would refuse any request, and the environment is compared before and
        after: constructing must make no request, change no variable and load
        nothing of the command's.
        """
        code = (
            "import json, os, sys; before = dict(os.environ); import phabfive; "
            "m = phabfive.Maniphest(url='http://127.0.0.1:9', "
            "token='api-" + "a" * 28 + "'); "
            "print(json.dumps({'built': m.phab.is_built, "
            "'environ': before == dict(os.environ), "
            "'loaded': [n for n in ('typer', 'click', 'InquirerPy', 'rich', "
            "'phabfive.cli') if n in sys.modules]}))"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("PHAB_")}
        env["HOME"] = str(tmp_path)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "built": False,
            "environ": True,
            "loaded": [],
        }

    def test_exceptions_do_not_import_the_client(self):
        """Catching phabfive's errors must not cost the Conduit client."""
        code = "import phabfive; phabfive.PhabfiveException"
        assert _loaded_after(code, ["phabricator", "requests"]) == "[]"
