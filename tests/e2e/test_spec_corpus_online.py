# -*- coding: utf-8 -*-
"""The shipped corpus, against a real instance: layer 2 (#463, Phase 4).

`tests/test_spec_corpus.py` puts every shipped spec through the **offline**
layer on every unit run, which settles shapes, keys, variables and local
ids. It cannot settle a name: whether `mikael.wallin` is a user, whether
`Development` is one project rather than three, whether `Backlog` is a
column of a board the item is tagged into. Those are the whole reason
validation has a second layer, and until this file nothing ran it over the
corpus at all - so a shipped example naming a project nobody has would have
been found by a reader running it, which is the failure this phase exists to
stop happening a seventh time.

Two passes, and the difference between them matters:

* `phabfive spec validate` over every file outside `specs/broken/`, which
  runs **both** layers and reports problems as records;
* `phabfive apply --dry-run` over `specs/create/**`, which runs the planner
  - a stricter reader than validation, because it also refuses a priority
  the instance does not define and an object type nothing creates.

**Nothing here ever applies a shipped example for real, and nothing should
ever make it.** A project cannot be deleted or archived through Conduit and
a hashtag can be taken only once, so the second run of an applying gate
would fail with `hashtag-taken` and the namespace would have to be reset to
get CI green again. `--dry-run` is not caution here; it is the only form of
this gate that can run twice.
"""

# python std lib
from pathlib import Path

# 3rd party imports
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CORPUS = REPO_ROOT / "specs"

#: Wrong on purpose, one file per problem code. `tests/test_spec_corpus.py`
#: excludes them from its walk for the same reason and
#: `tests/test_spec_broken_corpus.py` asserts what each one reports.
BROKEN = "broken"

#: Create specs `apply` refuses although both validation layers pass them,
#: with the reason each is refused for. Not a skip list of things that are
#: broken: each one is a documented gap in the *planner*, and the file is
#: shipped as the example of a format feature no reader implements yet.
#: `docs/phorge-spec.md`'s `## Implementations` section names the same gap.
#:
#: The assertion below is two-sided - the run must fail, and fail with this
#: reason - so the day the planner grows that object type this turns red and
#: the entry goes, rather than the gate quietly stopping covering a file.
#:
#: Empty since the paste builder landed, and deliberately kept: it is the
#: mechanism, and the next object type the format admits before the planner
#: creates it goes here rather than into a skip.
NOT_PLANNED: dict[str, str] = {}


def _specs(directory=None):
    """Every shipped spec, or every one under a subdirectory of the corpus."""
    root = CORPUS / directory if directory else CORPUS

    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".yaml", ".yml", ".json", ".jsonl", ".toml"}
        and BROKEN not in path.relative_to(CORPUS).parts
    )


def _name(path):
    return str(path.relative_to(CORPUS))


ALL_SPECS = _specs()
CREATE_SPECS = _specs("create")


def test_there_is_a_corpus_to_check():
    """Guard the guard: an empty glob passes every case under it."""
    assert len(ALL_SPECS) >= 20
    assert len(CREATE_SPECS) >= 5
    assert not any(BROKEN in path.parts for path in ALL_SPECS)


@pytest.mark.parametrize("path", ALL_SPECS, ids=_name)
def test_every_shipped_spec_validates_against_the_instance(path, phabfive_raw):
    """Both layers, so every name in the corpus is one the instance has."""
    result = phabfive_raw("spec", "validate", str(path))

    assert result.returncode == 0, (
        f"{_name(path)} does not validate against the live instance "
        f"(exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize("path", CREATE_SPECS, ids=_name)
def test_every_create_spec_plans(path, phabfive_raw):
    """`--dry-run`, never a real apply: see this module's docstring."""
    result = phabfive_raw("apply", "-f", str(path), "--dry-run")
    refusal = NOT_PLANNED.get(path.name)

    if refusal is None:
        assert result.returncode == 0, (
            f"{_name(path)} does not plan (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
        # The sentence is on stderr whenever the plan itself is on stdout as
        # records, which is every machine format - and a non-TTY run takes
        # one from PHAB_FALLBACK, so this is the normal case here.
        assert "would create" in result.stdout + result.stderr
        return

    assert result.returncode != 0, (
        f"{_name(path)} is listed in NOT_PLANNED but planned cleanly. "
        "If the planner grew that object type, delete the entry and say so "
        "in docs/phorge-spec.md's ## Implementations section."
    )
    assert refusal in result.stderr, (
        f"{_name(path)} was refused for a different reason than the one "
        f"recorded:\n{result.stderr}"
    )
