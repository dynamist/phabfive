# -*- coding: utf-8 -*-

"""docs/phorge-spec.md says what the declarations say, and names no command.

Two things this file guards, and both have already gone wrong here.

**The tables drift.** docs/search-templates.md documented `created-before` and
`updated-before` while the loader refused them, for three releases, because the
list of keys was written by hand beside a list of keys written by hand (#295).
The tables on the normative page are generated instead, by
scripts/gen_spec_docs.py, and the first test below asserts the checked-in page
equals what that script would write. `test_every_declared_key_is_documented` is
the same assertion from the other end and over all eight (object type, verb)
pairs rather than task/search alone, which is what it replaces in
tests/test_search_template_keys.py.

**The page stops being normative.** docs/create-templates.md described three
behaviours the code did not have, because format rules and usage guidance were
interleaved and restated. The rule that keeps this page honest is mechanical: it
defines the format and names no program, so no shell block and no invocation may
appear in it, and the one word naming an implementation belongs to the one
section about implementations.

The examples are executed rather than read: every complete spec in a fenced
block is parsed in the serialization its fence claims and validated offline,
which is what stops an example that cannot be run from being shipped as one.
"""

import json
import re
import sys
from pathlib import Path

import pytest
from typer.main import get_command

from phabfive.spec import parse_spec, validate_offline
from phabfive.spec.problems import CODES, Severity
from phabfive.spec.registry import spec_keys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gen_spec_docs  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "docs" / "phorge-spec.md"

#: The section whose prose may name an implementation. Everywhere else the
#: page is about the format, and a program's name in it is a rule that belongs
#: in a usage guide.
IMPLEMENTATION_SECTION = "## Implementations"

#: Fence info strings that mean "this is something to run in a terminal". A
#: normative page has none of them: anything phrased as "run this" is usage.
SHELL_FENCES = frozenset(
    {"bash", "sh", "shell", "console", "shell-session", "zsh", "fish", "terminal"}
)

#: How each fence's contents are parsed, for the examples that are whole specs.
SPEC_FENCES = {"yaml": "yaml", "json": "json", "jsonl": "jsonl", "toml": "toml"}

#: The body keys that make a block a document rather than a fragment. A block
#: showing one envelope key is an illustration of that key, not an example to
#: run, and a spec with no body cannot be validated at all.
BODY_KEYS = ("tasks", "projects", "pastes", "passphrases", "search", "searches")

#: The one spec the serialization section writes four ways.
PARITY_TITLE = "Rotate the staging database credentials"


def _page():
    return PAGE.read_text(encoding="utf-8")


def _fences(text):
    """Every fenced block, as (info string, contents)."""
    return [
        (match.group(1).strip().lower(), match.group(2))
        for match in re.finditer(r"^```([^\n]*)\n(.*?)^```", text, re.M | re.S)
    ]


def _outside_fences(text):
    """The page with every fenced block removed, for a prose-only assertion."""
    return re.sub(r"^```[^\n]*\n.*?^```", "", text, flags=re.M | re.S)


def _sections(text):
    """The page split into (heading, body) pairs at its `##` headings."""
    parts = re.split(r"^(## .+)$", text, flags=re.M)
    head = [("", parts[0])] if parts else []

    return head + list(zip(parts[1::2], parts[2::2]))


def _is_whole_spec(fence, body):
    """Whether a block is a complete document rather than a fragment.

    Decided from the content, so an example becomes checked by being complete
    rather than by being annotated - nothing can forget the annotation.
    """
    if fence not in SPEC_FENCES:
        return False

    if "phorge/v1alpha1" not in body:
        return False

    keys = "|".join(BODY_KEYS)

    if fence == "yaml":
        # Anywhere in the block, not only at its head: an example that opens
        # with a comment or with `metadata:` is as complete as one that opens
        # with `spec:`, and gating on the first line would skip it by an
        # accident of formatting rather than by what it contains.
        return bool(re.search(r"^spec: phorge/v1alpha1$", body, re.M)) and bool(
            re.search(rf"^(?:{keys}):", body, re.M)
        )

    if fence == "toml":
        return 'spec = "phorge/v1alpha1"' in body and bool(
            re.search(rf"^(?:\[\[(?:{keys})\]\]|(?:{keys}) *=)", body, re.M)
        )

    return '"spec"' in body and bool(re.search(rf'"(?:{keys})" *:', body))


def _examples():
    """Every complete spec on the page, with the format it is written in."""
    return [
        (SPEC_FENCES[fence], body)
        for fence, body in _fences(_page())
        if _is_whole_spec(fence, body)
    ]


def _documented_keys(object_type, verb):
    """The keys the page's generated table for one pair lists.

    Read off the rendered page rather than out of the generator, so a table
    that was hand-edited into the file fails this as well as the equality
    check above it.
    """
    text = _page()
    marker = re.escape(f"<!-- BEGIN GENERATED keys {object_type}/{verb} -->")
    end = re.escape(f"<!-- END GENERATED keys {object_type}/{verb} -->")
    block = re.search(rf"{marker}(.*?){end}", text, re.S)

    assert block, f"no generated block for {object_type}/{verb}"

    return {
        match.group(1)
        for match in re.finditer(r"^\| `([^`]+)` \|", block.group(1), re.M)
    }


def test_the_page_exists():
    # Guards every test below it: a missing file would make most of them
    # pass vacuously rather than fail.
    assert PAGE.is_file()
    assert "phorge/v1alpha1" in _page()


def test_the_generated_tables_are_current():
    """The checked-in page equals what the generator would write."""
    current = _page()

    assert current == gen_spec_docs.render(current), (
        "docs/phorge-spec.md is out of date with the declarations it "
        "documents. Run: python3 scripts/gen_spec_docs.py"
    )


@pytest.mark.parametrize(
    ("object_type", "verb"),
    gen_spec_docs.PAIRS,
    ids=lambda pair: pair if isinstance(pair, str) else "/".join(pair),
)
def test_every_declared_key_is_documented(object_type, verb):
    """A key nobody can find out about might as well not be declared."""
    assert _documented_keys(object_type, verb) == set(spec_keys(object_type, verb))


def test_the_structural_create_keys_are_documented():
    """The registry is not the whole create surface, and the page says so.

    `id`, `tasks`, `parent`, `parents` and `subtasks` are structure rather
    than values and are declared in `phabfive.spec.references`. A page
    generated from the registry alone would document an incomplete format.
    """
    block = re.search(
        r"<!-- BEGIN GENERATED create-structural -->(.*?)"
        r"<!-- END GENERATED create-structural -->",
        _page(),
        re.S,
    )

    assert block

    documented = {
        match.group(1)
        for match in re.finditer(r"^\| `([^`]+)` \|", block.group(1), re.M)
    }

    assert documented == {"id", "tasks", "parent", "parents", "subtasks"}


def test_every_problem_code_is_documented():
    """A code is what a program branches on, so all of them are written down."""
    block = re.search(
        r"<!-- BEGIN GENERATED codes -->(.*?)<!-- END GENERATED codes -->",
        _page(),
        re.S,
    )

    assert block

    documented = [
        match.group(1)
        for match in re.finditer(r"^\| `([^`]+)` \|", block.group(1), re.M)
    ]

    assert documented == list(CODES)


def test_the_page_carries_no_shell_block():
    """Anything phrased as "run this" belongs in a usage guide."""
    found = sorted({fence for fence, _ in _fences(_page()) if fence in SHELL_FENCES})

    assert not found, f"the normative page has shell blocks: {found}"


def test_the_page_carries_no_command_line():
    """No invocation of any command the CLI offers, in prose or in a fence.

    The command names come from the app rather than from a list here, so a
    command added later is covered without anyone remembering to add it.
    """
    from phabfive.cli import app

    names = set(get_command(app).commands)

    assert names, "the app has no commands, so this assertion proves nothing"

    pattern = re.compile(
        r"\bphabfive\s+(?:--?\w|{})\b".format("|".join(sorted(names))),
    )
    offenders = [
        f"{number}: {line.strip()}"
        for number, line in enumerate(_page().splitlines(), start=1)
        if pattern.search(line)
    ]

    assert not offenders, "the normative page invokes a command:\n" + "\n".join(
        offenders
    )


def test_the_tool_is_named_only_where_implementations_are_discussed():
    """One section may name an implementation. The rest is about the format."""
    offenders = [
        heading or "(preamble)"
        for heading, body in _sections(_outside_fences(_page()))
        if heading != IMPLEMENTATION_SECTION and re.search(r"\bphabfive\b", body, re.I)
    ]

    assert not offenders, (
        "the normative page names an implementation outside "
        f"{IMPLEMENTATION_SECTION!r}: {offenders}"
    )


def test_there_are_examples_to_check():
    # Guards the test below it: an empty list passes every case under it.
    examples = _examples()
    formats = {format for format, _ in examples}

    assert len(examples) >= 10
    assert formats == set(SPEC_FENCES.values())


@pytest.mark.parametrize(
    ("format", "body"),
    _examples(),
    ids=lambda value: value if value in SPEC_FENCES.values() else "",
)
def test_every_example_is_valid(format, body):
    """Every complete document on the page parses and validates offline.

    A documented example nobody runs is how the first bug of this whole
    effort survived for years.
    """
    spec = parse_spec(body, format=format, source="docs/phorge-spec.md")
    problems = [
        record
        for record in validate_offline(spec.render())
        if record.severity == Severity.ERROR
    ]

    assert not problems, [record.as_record() for record in problems]


def test_the_four_serializations_describe_the_same_spec():
    """The serialization section shows one spec four ways, and means it."""
    bodies = {}

    for format, body in _examples():
        if PARITY_TITLE not in body:
            continue

        bodies[format] = parse_spec(body, format=format).body

    assert set(bodies) == set(SPEC_FENCES.values()), sorted(bodies)
    assert len({json.dumps(body, sort_keys=True) for body in bodies.values()}) == 1


def test_the_page_is_in_the_navigation():
    """A page nothing links to is a page nobody reads."""
    nav = (REPO / "mkdocs.yml").read_text(encoding="utf-8")

    assert "phorge-spec.md" in nav


#: The keyword arguments a problem's slug is written as: `code=` on a
#: `Problem` (and on the `problem()` helper that builds one), `problem=` on a
#: resolution result, which `online.py` copies into the record's `code`.
SLUG_KEYWORDS = ("code", "problem")

#: Codes in the vocabulary that nothing in `phabfive/spec/` emits, and which
#: `## Implementations` therefore has to name as gaps in the reader rather
#: than leave a reader of the table to assume the check runs. Frozen here on
#: purpose: adding the missing check, or adding a code before its check,
#: fails this and makes the page say so in the same change.
UNEMITTED_CODES = frozenset({"unknown-priority", "unknown-status"})


def _emitted_codes():
    """Every problem slug written as a literal anywhere in `phabfive/spec/`.

    Read with `ast` rather than by grepping, so a slug inside a docstring or a
    comment - both of which name codes constantly - is not mistaken for a
    check that emits one.
    """
    import ast

    root = REPO / "phabfive" / "spec"
    found = set()

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            for keyword in node.keywords:
                if keyword.arg not in SLUG_KEYWORDS:
                    continue

                for part in ast.walk(keyword.value):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        found.add(part.value)

    return found


def test_every_code_the_reader_cannot_emit_is_named_as_a_gap():
    """The page must not assert a check that nothing runs.

    Three of the page's normative sentences claimed checks the reference
    implementation does not have, and the codes table listed the slugs beside
    the ones that work. The table is generated from `CODES` and so cannot
    drift on the *set*; nothing tied the set to the checks. This does.
    """
    emitted = _emitted_codes()

    # The two a command reports for itself: there is no spec to hang them on,
    # so `phabfive/spec/` is the wrong place to look for them.
    from phabfive.cli.spec_report import CODE_UNREACHABLE, CODE_UNREADABLE

    missing = {
        code
        for code in CODES
        if code not in emitted and code not in (CODE_UNREADABLE, CODE_UNREACHABLE)
    }

    assert missing == set(UNEMITTED_CODES), (
        "the set of codes nothing emits changed; update UNEMITTED_CODES and "
        f"the page's ## Implementations section together: {sorted(missing)}"
    )

    implementations = "".join(
        body
        for heading, body in _sections(_page())
        if heading.strip() == IMPLEMENTATION_SECTION
    )

    for code in sorted(UNEMITTED_CODES):
        assert f"`{code}`" in implementations, (
            f"{code} is in the vocabulary, nothing emits it, and "
            "## Implementations does not say so"
        )
