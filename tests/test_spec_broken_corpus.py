# -*- coding: utf-8 -*-

"""The specs that are wrong on purpose, and the code each one reports (#490).

`specs/broken/` holds one file per mistake people actually make, each named
after the `code` its failure is reported under. They are two things at once:
the fixtures these tests assert against, and documentation by example - a
reader who wants to know what a dangling `$ref` looks like opens the file
called `dangling-local-id.yaml` and reads the report beside it.

**The directory is excluded from the corpus walk** that requires every
shipped spec to validate cleanly, and the exclusion lives in that walk's
glob rather than in a skip list here - one fact in one place. If a run of
`tests/test_spec_corpus.py` ever fails naming a file under `specs/broken/`,
the glob is what to fix; these files are supposed to fail.

Three of the four are decided offline, because they are questions a file
answers about itself. The fourth - a user the instance does not have - is
not, and that is the whole reason validation has two layers. Its online pass
runs here against a mocked client, the way `tests/test_spec_validate_online.py`
does it: `Phabfive.__init__` is faked, `.phab` is a `MagicMock`, and no
socket is opened.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from phabfive.maniphest.core import Maniphest
from phabfive.spec import Severity, load_spec, validate_offline, validate_online
from phabfive.spec.problems import CODES, Layer

REPOSITORY = Path(__file__).resolve().parent.parent
BROKEN = REPOSITORY / "specs" / "broken"

#: Each file, and the one code it exists to produce. The file is named after
#: the code wherever the two can be spelled the same; `dangling-local-id`
#: reads better than `unknown-local-id` as a file name and is the one place
#: they differ, so it is written out rather than derived.
EXPECTED = {
    "undefined-variable.yaml": "undefined-variable",
    "duplicate-local-id.yaml": "duplicate-local-id",
    "dangling-local-id.yaml": "unknown-local-id",
    "unknown-user.yaml": "unknown-user",
}

#: The ones whose answer needs no instance.
OFFLINE = (
    "undefined-variable.yaml",
    "duplicate-local-id.yaml",
    "dangling-local-id.yaml",
)

NAMES = sorted(EXPECTED)


def _spec(name):
    return load_spec(BROKEN / name)


def _errors(problems):
    return [problem for problem in problems if problem.severity == Severity.ERROR]


def _phab_knowing_nobody():
    """A client with an empty user directory, and a caller for `@me`."""
    phab = MagicMock()
    phab.user.whoami.return_value = {
        "phid": "PHID-USER-caller",
        "userName": "caller",
    }
    phab.user.search.return_value = {"data": []}
    phab.project.search.return_value = {"data": []}
    phab.project.query.return_value = {"data": {}}
    return phab


def _app(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        app = Maniphest()

    app.phab = phab
    app.url = "http://phorge.example.com"
    app.conf = {}
    app.lookup_store = None
    return app


# --------------------------------------------------------------------------
# The set itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_the_broken_spec_is_shipped(name):
    """Guards everything below, and the guides quote these by name."""
    assert (BROKEN / name).is_file(), (
        f"specs/broken/{name} is the worked example of {EXPECTED[name]!r}"
    )


@pytest.mark.parametrize("name", NAMES)
def test_the_expected_code_is_in_the_vocabulary(name):
    """A fixture asserting a slug nothing emits would pass forever."""
    assert EXPECTED[name] in CODES


def test_nothing_else_is_in_the_directory_unasserted():
    """A broken spec nobody asserts against is a file that means nothing.

    It would also be the one thing the corpus walk cannot catch: everything
    here is excluded from it, so an unasserted file is checked by nobody.
    """
    shipped = {
        path.name
        for path in BROKEN.iterdir()
        if path.is_file()
        and path.suffix in {".yaml", ".yml", ".json", ".jsonl", ".toml"}
    }

    assert shipped == set(EXPECTED)


@pytest.mark.parametrize("name", NAMES)
def test_the_broken_spec_still_loads(name):
    """Wrong, not unreadable.

    A file that cannot be parsed raises rather than reporting a `Problem` -
    there is nothing to attach one to - so a fixture meant to produce a code
    has to get as far as validation.
    """
    assert _spec(name) is not None


# --------------------------------------------------------------------------
# What each one reports
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", OFFLINE)
def test_the_offline_fixture_reports_exactly_its_code(name):
    """One file, one mistake, one problem - so the report names the fix."""
    problems = _errors(validate_offline(_spec(name)))

    assert [problem.code for problem in problems] == [EXPECTED[name]], [
        f"{problem.code}: {problem.object}.{problem.field}: {problem.reason}"
        for problem in problems
    ]

    problem = problems[0]

    assert problem.layer == Layer.OFFLINE
    assert problem.object, "a problem names where in the spec it is"
    assert problem.reason.strip()


def test_the_unknown_user_fixture_is_clean_until_an_instance_is_asked():
    """Offline it is a well-formed username; only the instance knows.

    This is the fixture that shows why there are two layers at all. Asserted
    from the other end as well - that it reports nothing offline - because a
    file that failed offline would never reach the online pass and the
    online assertion below would be vacuous.
    """
    assert _errors(validate_offline(_spec("unknown-user.yaml"))) == []


def test_the_unknown_user_fixture_reports_every_missing_name_at_once():
    """Both names in one pass, which is the rule the online layer exists for."""
    spec = _spec("unknown-user.yaml")
    problems = _errors(validate_online(spec, _app(_phab_knowing_nobody())))

    assert problems, "the mocked instance knows nobody, so both names are unknown"
    assert {problem.code for problem in problems} == {"unknown-user"}
    assert all(problem.layer == Layer.ONLINE for problem in problems)

    fields = {problem.field for problem in problems}

    assert fields == {"assignment", "subscribers[0]"}, (
        f"one pass reports every unresolvable reference; got {sorted(fields)}"
    )
