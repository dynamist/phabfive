# -*- coding: utf-8 -*-

"""No file in the tree names a path that is not there any more (#491).

Six file paths the documentation told users to run had never existed, and
the shipped corpus was renamed twice. Both failures have the same shape: a
path written into prose, never executed, and true only on the day it was
typed. Three gates here, all mechanical:

1. **The old corpus name is gone.** `templates/` became `specs/`, with
   `task-create/` and `task-search/` becoming `create/` and `search/`, since
   the files are no longer task-only. A reference left behind points at
   nothing at all.
2. **Every `specs/` path anyone is told to run exists.** This is the gate
   that catches the failure that actually happened: a guide naming
   `specs/create/sprint-tasks.yaml` while the file sits somewhere else.
3. **Every link between documentation pages resolves.** Two pages were
   renamed in this phase and four links to them were left behind; mkdocs is
   not strict, so the built site answered 404 without anything going red.
4. **Every `#anchor` a link names is a heading on that page.** The same four
   links carried anchors onto pages that had been rewritten, and a link to a
   heading that is gone 404s exactly as quietly as a link to a page that is.
5. **Every page is in the mkdocs nav**, and
6. **every page is linked from `docs/index.md`.** A page nothing navigates
   to is a page nothing reads, which is how a normative definition of the
   format could ship and be wrong for a release. Three pages were added in
   this phase; both lists were correct by hand and by nothing else.

This file is excluded from the first gate for the obvious reason: it has to
write the forbidden spellings down to search for them.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directories that hold no source of ours, or hold generated copies of it.
SKIPPED_DIRECTORIES = {
    ".git",
    ".venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "site",
    "htmlcov",
    ".eggs",
}

#: The text files worth reading. A path can only go stale in something a
#: human wrote, and reading every byte in the tree would mean guessing at
#: encodings for no gain.
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".cfg",
    ".ini",
    ".txt",
    ".json",
    ".sh",
    ".rst",
}

TEXT_NAMES = {"Makefile", "Dockerfile", ".gitignore"}

#: The spellings the corpus had before #491, as *paths*. `task-create` on its
#: own is matched too: it was a directory name, and prose that still says it
#: is prose describing a layout that is gone.
STALE_PATTERNS = (
    re.compile(r"templates/"),
    re.compile(r"task-create"),
    re.compile(r"task-search"),
)

#: This file writes the stale spellings down in order to look for them.
SELF = Path(__file__).name

#: A changelog is a record of what changed, so an upgrade note has to be able
#: to say what a path was renamed *from*. "The corpus moved to specs/" helps
#: nobody who is looking for `templates/` because that is what their script
#: still says. Exempt here rather than reworded there.
HISTORICAL = {"CHANGELOG.md"}


def _text_files():
    """Every text file in the tree, skipping the generated directories."""
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue

        if SKIPPED_DIRECTORIES & set(path.relative_to(REPO_ROOT).parts[:-1]):
            continue

        if path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES:
            yield path


def _read(path):
    """The file's text, or None when it is not text after all."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


TEXT_FILES = sorted(_text_files())


def test_there_are_files_to_check():
    """Guard the guard: an empty walk would pass every case below."""
    assert len(TEXT_FILES) > 100

    names = {path.name for path in TEXT_FILES}

    assert "SKILL.md" in names
    assert "mkdocs.yml" in names
    assert "pyproject.toml" in names


def test_no_file_names_the_old_corpus():
    """`templates/` is gone, so nothing may still send a reader there."""
    offences = []

    for path in TEXT_FILES:
        if path.name == SELF or path.name in HISTORICAL:
            continue

        text = _read(path)

        if text is None:
            continue

        for number, line in enumerate(text.splitlines(), start=1):
            for pattern in STALE_PATTERNS:
                if pattern.search(line):
                    where = path.relative_to(REPO_ROOT)
                    offences.append(f"{where}:{number}: {line.strip()}")

    assert offences == [], "stale corpus paths:\n" + "\n".join(offences)


def test_the_corpus_directory_is_where_everything_says_it_is():
    """The other half: the new name is a directory that exists."""
    assert (REPO_ROOT / "specs").is_dir()
    assert (REPO_ROOT / "specs" / "create").is_dir()
    assert (REPO_ROOT / "specs" / "search").is_dir()
    assert not (REPO_ROOT / "templates").exists()


#: Where a reader is told to run something: the documentation, the readme and
#: the agent skill. A spec path in a *test* is exercised by the test itself.
PROSE_FILES = sorted(
    [*(REPO_ROOT / "docs").glob("*.md"), REPO_ROOT / "README.md"]
    + [REPO_ROOT / "phabfive" / "SKILL.md"]
)

#: The package's own source, for the same gate: a `--help` example naming a
#: spec is a line a user copies straight off the terminal, and it was written
#: before the corpus was in place at least once.
SOURCE_FILES = sorted((REPO_ROOT / "phabfive").rglob("*.py"))

#: The corpus itself. A shipped spec cites its neighbours - in a header
#: comment, and in `metadata.description`, which is *printed* when the spec
#: runs - so a path in one of them is as public as a path on a page.
CORPUS_FILES = sorted(
    path
    for path in (REPO_ROOT / "specs").rglob("*")
    if path.is_file()
    and path.suffix in {".yaml", ".yml", ".json", ".jsonl", ".toml", ".md"}
)

#: A path under `specs/`, as prose writes one: bare, in backticks, or as the
#: argument of a command. The suffix is required, so `specs/` and
#: `specs/create/` - which name the directory rather than a file - are not
#: matched.
SPEC_PATH = re.compile(r"specs/[A-Za-z0-9._/-]+\.(?:yaml|yml|json|jsonl|toml)")


@pytest.mark.parametrize(
    "path",
    PROSE_FILES + SOURCE_FILES + CORPUS_FILES,
    ids=lambda path: str(path.relative_to(REPO_ROOT)),
)
def test_every_spec_path_in_prose_exists(path):
    """A documented example nobody runs is how the last six got in.

    `specs/create/sprint-tasks.yaml` written on a page is a promise that the
    reader can copy the line and have it work. The corpus gate proves the
    file loads; this proves the page names the file.
    """
    text = _read(path)

    assert text is not None, f"{path} is not readable as text"

    missing = sorted(
        {
            named
            for named in SPEC_PATH.findall(text)
            if not (REPO_ROOT / named).is_file()
        }
    )

    assert missing == [], (
        f"{path.relative_to(REPO_ROOT)} names spec files that do not exist: {missing}"
    )


#: A markdown link to a local file: `[text](page.md)`, with an optional
#: anchor. An absolute URL has a scheme and is not matched.
MARKDOWN_LINK = re.compile(r"\]\(([^)\s:#]+\.md)(#[^)\s]*)?\)")


def _slug(heading):
    """The anchor mkdocs gives a heading, by its default slugify.

    Lowercased, punctuation dropped, spaces to hyphens. Close enough for a
    gate: the headings on these pages are words, and anything this does not
    reproduce would have to be a heading with characters no page here uses.
    """
    text = heading.lstrip("#").strip()
    text = re.sub(r"`|\*|_(?=\S)|(?<=\S)_", "", text)
    text = re.sub(r"[^\w\s-]", "", text.lower())

    return re.sub(r"[\s]+", "-", text.strip())


def _anchors(path):
    """Every `#anchor` a page offers, from its headings."""
    text = _read(path) or ""

    return {_slug(line) for line in text.splitlines() if re.match(r"^#{1,6} ", line)}


@pytest.mark.parametrize("path", PROSE_FILES, ids=lambda path: path.name)
def test_every_link_between_pages_resolves(path):
    """mkdocs is not strict here, so a renamed page 404s in silence."""
    text = _read(path)

    assert text is not None, f"{path} is not readable as text"

    missing = sorted(
        {
            target
            for target, _ in MARKDOWN_LINK.findall(text)
            if not (path.parent / target).is_file()
            and not (REPO_ROOT / target).is_file()
        }
    )

    assert missing == [], (
        f"{path.relative_to(REPO_ROOT)} links to pages that do not exist: {missing}"
    )


@pytest.mark.parametrize("path", PROSE_FILES, ids=lambda path: path.name)
def test_every_anchor_a_link_names_is_a_heading(path):
    """A renamed *heading* 404s just as silently as a renamed page.

    This phase rewrote two pages and retargeted four links onto them,
    anchors and all; the link gate above discarded the anchor, so it was
    passing on a case it was not checking.
    """
    text = _read(path)

    assert text is not None, f"{path} is not readable as text"

    missing = []

    for target, anchor in MARKDOWN_LINK.findall(text):
        if not anchor:
            continue

        page = path.parent / target

        if not page.is_file():
            page = REPO_ROOT / target

        if not page.is_file():
            # The link gate above already names this one.
            continue

        if anchor.lstrip("#") not in _anchors(page):
            missing.append(f"{target}{anchor}")

    assert sorted(set(missing)) == [], (
        f"{path.relative_to(REPO_ROOT)} links to headings that do not exist: "
        f"{sorted(set(missing))}"
    )


#: Pages that are deliberately reachable only from another page, not from
#: the index and not from the nav. Empty today, and named here rather than
#: left implicit so that excluding one is a decision somebody wrote down.
UNLISTED_PAGES: frozenset = frozenset()

DOC_PAGES = (
    frozenset(
        path.name
        for path in sorted((REPO_ROOT / "docs").glob("*.md"))
        if path.name != "index.md"
    )
    - UNLISTED_PAGES
)


def test_there_are_doc_pages():
    """Guard the guard, again: an empty set passes both cases below."""
    assert len(DOC_PAGES) > 5
    assert "phorge-spec.md" in DOC_PAGES


def test_every_page_is_in_the_mkdocs_nav():
    """A page outside the nav is not in the built site's sidebar at all.

    `docs/phorge-spec.md` is the normative definition of the format and was
    added in this phase along with two guides; three new pages is exactly
    when a nav entry gets forgotten, and nothing failed if one had been.
    """
    nav = (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    listed = set(re.findall(r"([A-Za-z0-9_.-]+\.md)", nav))

    assert sorted(DOC_PAGES - listed) == []


def test_index_links_every_page():
    """`docs/index.md` is the page a reader lands on; it names the rest."""
    index = (REPO_ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    linked = {target for target, _ in MARKDOWN_LINK.findall(index)}

    assert sorted(DOC_PAGES - linked) == []
