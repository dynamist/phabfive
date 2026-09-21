# -*- coding: utf-8 -*-
"""What each of phabfive's exceptions is, and what catches it."""

import pickle

import pytest

from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConfigException,
    PhabfiveConnectionException,
    PhabfiveDataException,
    PhabfiveException,
    PhabfiveInputException,
    PhabfiveNotFoundException,
    PhabfiveRemoteException,
)


@pytest.mark.parametrize(
    ("exception", "bases"),
    [
        (PhabfiveInputException, (PhabfiveConfigException, ValueError)),
        (PhabfiveNotFoundException, (PhabfiveDataException, LookupError)),
        (PhabfiveAPIException, (PhabfiveRemoteException,)),
        (PhabfiveConnectionException, (PhabfiveRemoteException,)),
    ],
)
def test_existing_handlers_still_catch_it(exception, bases):
    """Each new type lands in a handler that already existed for its kind."""
    assert issubclass(exception, PhabfiveException)
    for base in bases:
        assert issubclass(exception, base)


class TestAPIException:
    def test_reads_like_the_apierror_it_replaces(self):
        error = PhabfiveAPIException("ERR-INVALID-AUTH", "API token is not valid.")

        assert str(error) == "ERR-INVALID-AUTH: API token is not valid."
        assert error.code == "ERR-INVALID-AUTH"
        assert error.message == "API token is not valid."

    def test_survives_pickling(self):
        """Exceptions cross process boundaries; the args must rebuild it."""
        error = pickle.loads(pickle.dumps(PhabfiveAPIException("ERR-X", "boom")))

        assert (error.code, error.message) == ("ERR-X", "boom")


def test_the_module_needs_nothing_but_the_standard_library():
    import ast
    import sys
    from pathlib import Path

    import phabfive.exceptions

    tree = ast.parse(Path(phabfive.exceptions.__file__).read_text())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= set(sys.stdlib_module_names)


def test_library_code_raises_typed_exceptions():
    """No bare ValueError or PhabfiveException outside the command.

    Each says nothing about what went wrong, so a program cannot tell a bad
    argument from a missing object, and no command handler catches either.
    phabfive/cli/ is the command's own and exempt.
    """
    import ast
    from pathlib import Path

    import phabfive

    root = Path(phabfive.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts[0] == "cli":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
                continue
            name = getattr(node.exc.func, "id", None)
            if name in ("ValueError", "PhabfiveException"):
                offenders.append(f"{relative}:{node.lineno} raises {name}")
    assert offenders == []


def test_a_missing_create_template_is_an_error(tmp_path):
    """It used to log and return None, so the command exited 0."""
    from unittest import mock

    from phabfive.maniphest import Maniphest

    with mock.patch("phabfive.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()

    with pytest.raises(PhabfiveConfigException, match="does not exist"):
        maniphest.create_tasks_from_yaml(str(tmp_path / "missing.yaml"))
