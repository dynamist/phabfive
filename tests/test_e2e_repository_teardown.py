# -*- coding: utf-8 -*-

"""`create_repository`'s teardown must never deactivate a repository that is
still importing.

Phorge runs no daemons for an inactive repository, so `isImporting` freezes
true the moment one is deactivated mid-import, and nothing ever clears it.
The teardown waited 60 seconds for the import to finish and then deactivated
the repository **either way**: `while time.monotonic() < deadline` exits
normally on a timeout, so the edit after the loop ran whether or not the wait
had succeeded. Nine repositories on the development instance were made that
way, every one named `e2e-`, and each one cost `settled_repositories` its
full 180-second wait on every later run until #497 taught it to skip them
(#500).

Driven here with a fake conduit and a fake clock rather than against a live
Phorge, so it runs in CI where no instance exists - and so the timeout case,
which a real run reaches only when the daemons are slow, is reached every
time.
"""

import importlib.util
import pathlib
import warnings

import pytest

_CONFTEST = pathlib.Path(__file__).resolve().parent / "e2e" / "conftest.py"


def _e2e_conftest():
    """The e2e conftest as a module, without pytest collecting it."""
    spec = importlib.util.spec_from_file_location("e2e_conftest_probe", _CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_teardown(monkeypatch, *, importing_for):
    """Create one repository and tear it down, with time under our control.

    Parameters
    ----------
    importing_for : int
        How many polls report the repository as still importing. A number
        larger than the wait allows is the timeout case.

    Returns
    -------
    list
        The `phabfive(...)` argument tuples the teardown issued.
    """
    module = _e2e_conftest()
    calls = []
    clock = {"now": 0.0}
    polls = {"n": 0}

    def phabfive(*args):
        calls.append(args)
        return ""

    def conduit(method, **kwargs):
        polls["n"] += 1
        importing = polls["n"] <= importing_for
        return {"data": [{"fields": {"isImporting": importing}}]}

    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        module.time, "sleep", lambda s: clock.__setitem__("now", clock["now"] + s)
    )

    generator = module.create_repository.__wrapped__(phabfive, conduit)
    create = next(generator)
    create()
    calls.clear()  # the create itself is not what this is about

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(StopIteration):
            next(generator)

    return calls, [str(one.message) for one in caught]


def test_a_settled_repository_is_deactivated(monkeypatch):
    """The tidy-up still happens when the import finished."""
    calls, warned = _run_teardown(monkeypatch, importing_for=0)

    assert [one for one in calls if "--status=inactive" in one]
    assert warned == []


def test_a_repository_still_importing_is_left_active(monkeypatch):
    """The point of #500: deactivating here freezes isImporting forever.

    Active is the recoverable state - the daemons keep working and it settles
    on its own. Inactive is the trap.
    """
    calls, warned = _run_teardown(monkeypatch, importing_for=10_000)

    assert not [one for one in calls if "--status=inactive" in one], (
        "a repository that never settled was deactivated, which freezes "
        "isImporting true for good"
    )
    assert warned, "leaving it active is worth a word, or nobody ever tidies it"
    assert "#500" in warned[0]


def test_it_waits_rather_than_deactivating_immediately(monkeypatch):
    """A repository that settles on a later poll is still tidied away."""
    calls, warned = _run_teardown(monkeypatch, importing_for=3)

    assert [one for one in calls if "--status=inactive" in one]
    assert warned == []
