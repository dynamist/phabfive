# -*- coding: utf-8 -*-

"""Every written exit-status table says what `spec_report` says (#486).

Three commands answer for a spec file - `phabfive spec validate`,
`phabfive apply` and `phabfive search` - and the statuses they leave with are
one table, defined once in `phabfive.cli.spec_report`. The table is also
*written out* in six places a person or an agent reads: the two `--help`
docstrings in `phabfive/cli/spec_run.py`, the two guides, the agent skill,
and `spec_report`'s own module docstring.

That is the exact shape the previous set drifted in. The first review of this
phase found code 2 described as "a reference does not resolve, or the first
object was refused" in one place and "the online layer failed: a reference
does not resolve, or the instance refused what was asked" in another - the
same number, two meanings, and nothing comparing them.

So this compares them. Two properties, both mechanical:

1. **The numbers are the constants.** A table that documents a 5, or that
   loses the 4, fails - so `EXIT_*` cannot gain a member without the prose
   gaining a row.
2. **A number means the same thing everywhere it is written.** Each status
   has one keyword below that any description of it has to contain. The
   wording is free - `apply`'s 0 is "everything was created" and `search`'s
   is "every search ran", and both are right - but a row that stops saying
   *online* for 2, or *partway* for 4, fails.

A test cannot decide that prose is good. It can decide that six copies of
one table have not come apart, which is the failure that happened.
"""

import re
from pathlib import Path

import pytest

from phabfive.cli.spec_report import (
    EXIT_CLEAN,
    EXIT_OFFLINE,
    EXIT_ONLINE,
    EXIT_PARTIAL,
    EXIT_UNREACHABLE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The word any description of a status has to carry, whatever else it says.
#: One word per status, chosen to be the thing that distinguishes it: what
#: 1 and 2 are *for* is telling a wrong file from a wrong instance, so 1 says
#: "offline"/"read" and 2 says "online".
MEANS = {
    EXIT_CLEAN: ("created", "ran", "clean", "planned"),
    EXIT_OFFLINE: ("offline", "read"),
    EXIT_ONLINE: ("online",),
    EXIT_UNREACHABLE: ("instance",),
    EXIT_PARTIAL: ("partway",),
}

#: What each place is expected to document. `spec validate` has no 4, and
#: nothing else may leave it out.
FULL = frozenset(MEANS)
NO_PARTIAL = FULL - {EXIT_PARTIAL}


def _rows(text):
    """Every `<digit>  <description>` row of a status table in some text.

    Three spellings are in the tree and all three are one row per line with
    the number first: the `--help` docstrings indent `    0  ...`, the
    reStructuredText table in `spec_report` writes `  0  ...`, and the guides
    write a markdown `| 0 | ... |`. A number on its own line without a
    description is not a row.
    """
    rows = {}

    for line in text.splitlines():
        stripped = line.strip()

        markdown = re.match(r"^\|\s*(\d)\s*\|\s*(.+?)\s*\|\s*$", stripped)

        if markdown:
            rows.setdefault(int(markdown.group(1)), []).append(markdown.group(2))
            continue

        plain = re.match(r"^(\d)\s\s+(\S.*)$", stripped)

        if plain:
            rows.setdefault(int(plain.group(1)), []).append(plain.group(2))

    return rows


def _continued(text, rows):
    """Fold a row's continuation lines into it.

    The `--help` tables wrap, and the wrapped half carries the keyword as
    often as the first line does: `1  the file could not be read, is a search
    spec, or the offline / layer found an error`.
    """
    lines = text.splitlines()
    folded = {number: list(bodies) for number, bodies in rows.items()}
    current = None

    for line in lines:
        stripped = line.strip()
        start = re.match(r"^\|?\s*(\d)[\s|]", stripped)

        if start and int(start.group(1)) in folded:
            current = int(start.group(1))
            continue

        if current is None or not stripped:
            current = None
            continue

        if re.match(r"^[-=|]", stripped) or stripped.startswith("\\b"):
            current = None
            continue

        folded[current][-1] += " " + stripped

    return folded


def _table(text):
    """The status table some text carries, rows folded."""
    rows = _rows(text)

    return _continued(text, rows)


def _spec_run_docstring(name):
    """One command's `--help` text, from the module rather than from click."""
    from phabfive.cli import spec_run

    return getattr(spec_run, name).__doc__


def _read(relative):
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _section(text, heading):
    """The part of a markdown page under one heading, up to the next one."""
    parts = re.split(r"^(#{2,4} .+)$", text, flags=re.M)

    for title, body in zip(parts[1::2], parts[2::2]):
        if title.strip().endswith(heading):
            return body

    raise AssertionError(f"no section named {heading!r}")


#: Every place the table is written, what it is expected to hold, and how to
#: get at it. The skill's copy is prose rather than a table, so it is matched
#: as a whole below instead of row by row.
TABLES = {
    "spec_report module docstring": (
        lambda: __import__("phabfive.cli.spec_report", fromlist=["x"]).__doc__,
        FULL,
    ),
    "apply --help": (lambda: _spec_run_docstring("apply_command"), FULL),
    "search --help": (lambda: _spec_run_docstring("search_command"), NO_PARTIAL),
    "spec validate --help": (
        lambda: __import__("phabfive.cli.spec", fromlist=["x"]).spec_validate.__doc__,
        NO_PARTIAL,
    ),
    "docs/create-specs.md": (
        lambda: _section(_read("docs/create-specs.md"), "Exit status"),
        FULL,
    ),
    "docs/search-specs.md": (
        lambda: _section(_read("docs/search-specs.md"), "Exit status"),
        NO_PARTIAL,
    ),
}


def test_the_constants_are_what_the_tables_document():
    """Guard the guard: a renumbered constant must not pass silently."""
    assert sorted(MEANS) == [0, 1, 2, 3, 4]
    assert (
        EXIT_CLEAN,
        EXIT_OFFLINE,
        EXIT_ONLINE,
        EXIT_UNREACHABLE,
        EXIT_PARTIAL,
    ) == (0, 1, 2, 3, 4)


@pytest.mark.parametrize("where", sorted(TABLES))
def test_a_written_table_documents_the_right_statuses(where):
    """No row for a status that does not exist, and none missing."""
    source, expected = TABLES[where]
    table = _table(source())

    assert set(table) == set(expected), (
        f"{where} documents statuses {sorted(table)}, expected {sorted(expected)}"
    )


@pytest.mark.parametrize("where", sorted(TABLES))
def test_a_written_table_means_the_same_as_every_other(where):
    """A status says the same thing wherever it is written down."""
    source, expected = TABLES[where]
    table = _table(source())
    wrong = []

    for status, bodies in sorted(table.items()):
        for body in bodies:
            if not any(word in body.lower() for word in MEANS[status]):
                wrong.append(f"{status}: {body!r}")

    assert wrong == [], (
        f"{where} describes a status in terms nothing else uses; one of "
        f"{ {status: MEANS[status] for status in sorted(table)} } has to "
        f"appear:\n" + "\n".join(wrong)
    )


def test_the_skill_says_the_same_thing():
    """`SKILL.md` writes the table as a sentence, so it is matched as one.

    An agent branches on these numbers, so the words beside each one are as
    load-bearing here as in a table - they are just not in a table, and
    rewriting them into one to satisfy a test would be the test deciding how
    the file reads.
    """
    text = _read("phabfive/SKILL.md")
    start = text.index("Branch on the exit status")
    paragraph = text[start : text.index("\n\n", start)].lower()

    for status, words in sorted(MEANS.items()):
        assert f"`{status}`" in paragraph, f"SKILL.md does not name status {status}"

    for status in (EXIT_ONLINE, EXIT_PARTIAL):
        assert any(word in paragraph for word in MEANS[status]), (
            f"SKILL.md names status {status} without saying "
            f"{' or '.join(MEANS[status])}"
        )
