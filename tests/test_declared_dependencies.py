# -*- coding: utf-8 -*-

"""Every third-party module phabfive imports is declared in pyproject.toml.

phabfive imported click without declaring it, and got away with it for as
long as typer happened to depend on click. typer 0.27 dropped that
dependency, and every fresh install broke at once:

    $ phabfive --help
    File "phabfive/cli/shell_completion.py", line 7, in <module>
        from click.shell_completion import CompletionItem
    ModuleNotFoundError: No module named 'click'

The test suite could not see it, because uv.lock pins a typer old enough to
still pull click in. An undeclared dependency is only ever visible in a
fresh resolve, which is what the released wheel and the PyInstaller build
do -- so check the declaration itself rather than the installed environment.
"""

import ast
import pathlib
import re
import sys
from importlib.metadata import packages_distributions

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None

pytestmark = pytest.mark.skipif(
    tomllib is None, reason="reading pyproject.toml needs tomllib (Python 3.11+)"
)

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The distribution name at the front of a requirement such as "click>=8.0"
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def _normalize(name: str) -> str:
    """Compare distribution names the way PEP 503 does."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_distributions() -> set:
    with open(_PROJECT_ROOT / "pyproject.toml", "rb") as stream:
        pyproject = tomllib.load(stream)

    project = pyproject["project"]
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)

    return {
        _normalize(match.group())
        for match in filter(None, map(_REQUIREMENT_NAME.match, requirements))
    }


def _imported_top_level_modules() -> set:
    """Top-level names of every absolute import under phabfive/."""
    modules = set()

    for path in sorted((_PROJECT_ROOT / "phabfive").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])

    return modules - set(sys.stdlib_module_names) - {"phabfive"}


def _candidate_distributions(module: str) -> list:
    """Distributions a top-level module could come from.

    An optional extra such as ptpython need not be installed for the test to
    run, so fall back to the module name when nothing provides it here.
    """
    return packages_distributions().get(module) or [module]


def test_finds_the_imports():
    """Guard the guard: a broken scan would make the next test vacuous."""
    modules = _imported_top_level_modules()

    assert {"click", "typer", "rich"} <= modules


@pytest.mark.parametrize("module", sorted(_imported_top_level_modules()))
def test_import_is_declared(module):
    declared = _declared_distributions()
    candidates = _candidate_distributions(module)

    assert any(_normalize(name) in declared for name in candidates), (
        f"phabfive imports {module!r}, provided by "
        f"{' or '.join(candidates)}, but pyproject.toml does not declare it. "
        "It works only while some other dependency happens to pull it in."
    )
