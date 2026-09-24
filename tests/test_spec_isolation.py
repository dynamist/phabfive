# -*- coding: utf-8 -*-

"""Offline validation is really offline, proved out of process.

A repository of specs has to be checkable in CI or a pre-commit hook on a
machine that has never seen a Phorge token. That is not a thing to assert in
process: by the time the rest of the suite runs, `phabfive.core`,
`phabricator` and `requests` are already in `sys.modules` and `TYPER_USE_RICH`
is already in the environment, so every in-process version of these checks
passes whatever the code does.

So each one runs a fresh interpreter with `subprocess.run`, with the
environment scrubbed down to what an interpreter needs to start:

1. **No configuration, no credentials.** `PHAB_URL` and `PHAB_TOKEN` are
   unset and `HOME` points nowhere, so `~/.arcrc` and
   `~/.config/phabfive.yaml` do not exist. A spec still loads and still
   validates.
2. **Nothing forbidden is imported.** After loading and validating a spec,
   none of `phabricator`, `requests`, `phabfive.cli`, `phabfive.core` or
   `phabfive.conduit` is in `sys.modules`, and `TYPER_USE_RICH` is not in the
   environment. This is the strong one: it pins the import hygiene of every
   module in the subpackage, not only the two this issue wrote.
3. **No socket.** `socket.socket.connect` is replaced with something that
   raises before the call, so an attempt to open one fails loudly rather than
   timing out.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parent.parent

SEARCH_TEMPLATE = REPOSITORY / "specs" / "search" / "blocked-tasks.yaml"
CREATE_TEMPLATE = REPOSITORY / "specs" / "create" / "sprint-tasks.yaml"

# Every module under phabfive/spec/, so test 2 pins the whole subpackage
# rather than only the two files this issue owns.
SPEC_MODULES = sorted(
    f"phabfive.spec.{path.stem}"
    for path in (REPOSITORY / "phabfive" / "spec").glob("*.py")
    if path.stem != "__init__"
)

# What must not be reachable from the offline path. `phabfive.cli` sets
# TYPER_USE_RICH in os.environ as it imports, and the other three are the
# network.
FORBIDDEN = (
    "phabricator",
    "requests",
    "phabfive.cli",
    "phabfive.core",
    "phabfive.conduit",
)


def scrubbed_environment():
    """As little environment as an interpreter needs to start.

    No `PHAB_URL`, no `PHAB_TOKEN`, and a `HOME` that does not exist, so
    nothing can read `~/.arcrc`, `~/.config/phabfive.yaml` or a cache
    directory. `SYSTEMROOT` is what Windows needs to start Python at all, and
    `PATH` is kept because `uv run` put the interpreter on it.
    """
    keep = ("PATH", "SYSTEMROOT", "LD_LIBRARY_PATH", "VIRTUAL_ENV")
    environment = {name: os.environ[name] for name in keep if name in os.environ}

    nowhere = str(Path(os.sep, "nonexistent"))

    environment["HOME"] = nowhere
    # Windows names the places a configuration could come from with its own
    # variables, and they are pointed at the same nowhere rather than left
    # out. `phabricator/__init__.py` reads `os.environ['ProgramData']` (:52)
    # and `os.environ['AppData']` (:60) as it imports, so an *absent* one is
    # a KeyError before anything is validated, not an empty directory.
    # Scrubbing them entirely stopped the interpreter starting at all, which
    # is not the thing under test.
    environment["USERPROFILE"] = nowhere
    environment["APPDATA"] = nowhere
    environment["LOCALAPPDATA"] = nowhere
    environment["ProgramData"] = nowhere
    environment["ALLUSERSPROFILE"] = nowhere
    environment["PYTHONPATH"] = str(REPOSITORY)
    environment["TERM"] = "dumb"

    assert "PHAB_URL" not in environment
    assert "PHAB_TOKEN" not in environment
    assert "TYPER_USE_RICH" not in environment

    return environment


def run(script):
    """Run one script in a fresh interpreter with nothing to read."""
    return subprocess.run(
        [sys.executable, "-c", script],
        env=scrubbed_environment(),
        cwd=str(REPOSITORY),
        capture_output=True,
        text=True,
        timeout=120,
    )


def assert_clean(result):
    """A non-zero exit here is the whole finding, so show why."""
    assert result.returncode == 0, (
        f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


class TestNoConfigurationAndNoCredentials:
    """A machine with no token can still check a repository of specs."""

    @pytest.mark.parametrize(
        "path,kind",
        [(SEARCH_TEMPLATE, "search"), (CREATE_TEMPLATE, "create")],
    )
    def test_a_shipped_template_validates(self, path, kind):
        script = f"""
import json, os, sys
assert "PHAB_URL" not in os.environ, "PHAB_URL leaked into the subprocess"
assert "PHAB_TOKEN" not in os.environ, "PHAB_TOKEN leaked into the subprocess"
assert not os.path.exists(os.path.expanduser("~")), "HOME exists after all"

from phabfive.spec import load_spec, validate_offline

spec = load_spec({str(path)!r}, kind={kind!r})
print(json.dumps([one.as_record() for one in validate_offline(spec)]))
"""
        result = run(script)

        assert_clean(result)
        assert json.loads(result.stdout) == []

    def test_a_bad_spec_reports_its_problems_without_a_token(self):
        script = """
import json
from phabfive.spec import parse_spec, validate_offline

spec = parse_spec(
    "kind: search\\nsearches:\\n  - search: {limit: lots, colum: x}\\n",
    format="yaml",
)
print(json.dumps([one.code for one in validate_offline(spec)]))
"""
        result = run(script)

        assert_clean(result)
        assert sorted(json.loads(result.stdout)) == ["unknown-key", "wrong-type"]

    def test_the_schema_is_generated_without_a_token(self):
        script = """
import json
from phabfive.spec import build_schema

print(json.dumps(sorted(build_schema("search")["properties"])))
"""
        result = run(script)

        assert_clean(result)
        assert "searches" in json.loads(result.stdout)


class TestNothingForbiddenIsImported:
    """The strong one: it pins the whole subpackage's import hygiene."""

    def test_loading_and_validating_imports_nothing_forbidden(self):
        script = f"""
import json, os, sys

from phabfive.spec import load_spec, validate_offline

spec = load_spec({str(SEARCH_TEMPLATE)!r}, kind="search")
validate_offline(spec)

print(json.dumps({{
    "forbidden": [name for name in {FORBIDDEN!r} if name in sys.modules],
    "typer_use_rich": "TYPER_USE_RICH" in os.environ,
}}))
"""
        result = run(script)

        assert_clean(result)
        assert json.loads(result.stdout) == {"forbidden": [], "typer_use_rich": False}

    def test_importing_every_spec_module_imports_nothing_forbidden(self):
        """Not only the modules the offline path happens to touch."""
        script = f"""
import importlib, json, os, sys

for name in {SPEC_MODULES!r}:
    importlib.import_module(name)

print(json.dumps({{
    "modules": {SPEC_MODULES!r},
    "forbidden": [name for name in {FORBIDDEN!r} if name in sys.modules],
    "typer_use_rich": "TYPER_USE_RICH" in os.environ,
}}))
"""
        result = run(script)

        assert_clean(result)
        report = json.loads(result.stdout)

        assert report["forbidden"] == []
        assert report["typer_use_rich"] is False
        assert "phabfive.spec.validate" in report["modules"]
        assert "phabfive.spec.schema" in report["modules"]

    def test_the_environment_is_not_written(self):
        script = f"""
import json, os

before = dict(os.environ)

from phabfive.spec import load_spec, validate_offline, build_schema

validate_offline(load_spec({str(CREATE_TEMPLATE)!r}, kind="create"))
build_schema("create")

print(json.dumps(sorted(set(os.environ) ^ set(before))))
"""
        result = run(script)

        assert_clean(result)
        assert json.loads(result.stdout) == []


class TestNoSocket:
    """An attempt to reach the network fails loudly rather than timing out."""

    def test_validating_opens_no_connection(self):
        script = f"""
import json, socket


def refuse(*args, **kwargs):
    raise AssertionError("offline validation opened a socket")


socket.socket.connect = refuse
socket.socket.connect_ex = refuse
socket.create_connection = refuse

from phabfive.spec import load_spec, validate_offline

for path, kind in (({str(SEARCH_TEMPLATE)!r}, "search"), ({str(CREATE_TEMPLATE)!r}, "create")):
    validate_offline(load_spec(path, kind=kind))

print(json.dumps("no socket"))
"""
        result = run(script)

        assert_clean(result)
        assert json.loads(result.stdout) == "no socket"
