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
