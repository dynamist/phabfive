# -*- coding: utf-8 -*-

"""Every command line in the documentation is one the program has (#491).

`phabfive/SKILL.md` is what `phabfive --skill` prints into an agent's
context, so a line in it is not documentation an agent might read - it is an
instruction an agent will run. It has been wrong twice, both times in the
direction that makes an agent act confidently on a behaviour the code does
not have: it said a create template could hold several `---` documents when
`.load()` read one, and that a command line option overrode a template value
when `--with` ignored every other option.

Rewriting the prose does not stop the third one. What stops it is that the
part of the file an agent copies verbatim is checked, so this walks every
fenced block, takes every line that invokes `phabfive`, and resolves it
against the real Typer application:

* the command path exists - `maniphest search` resolves, a command removed
  or renamed does not;
* every option spelling on it is one that command or a group above it
  declares, which is what catches a flag that was dropped;
* every repository path named on the line is a file that exists, which is
  what would have caught the create example the file named before #491 -
  a path under the old corpus directory that had never existed at all.

The same walk runs over `docs/*.md` and `README.md`, for the same reason and
because they fail the same way: `phabfive maniphest show T123 --all` and
`--pp` were on two pages for as long as anyone had been reading them, and
neither option has ever existed on that command. A page is read by a person
rather than by an agent, which makes the failure slower and not smaller.

Nothing is executed and nothing is asked of an instance: this is click's own
resolution walk over the command tree.
"""

import re
import shlex
from pathlib import Path

import pytest
import typer

from phabfive.cli import app
from phabfive.constants import MONOGRAM_SHORTCUT

REPO_ROOT = Path(__file__).resolve().parent.parent

SKILL = REPO_ROOT / "phabfive" / "SKILL.md"

#: A monogram shortcut such as T123: `phabfive T123` is a command line, and
#: the root group resolves it through `MonogramGroup.resolve_command`.
MONOGRAM = re.compile(r"^[" + "".join(MONOGRAM_SHORTCUT) + r"]\d[\d,]*$")

#: A placeholder the reader is meant to replace, written `<name>`.
PLACEHOLDER = re.compile(r"^<.*>$")

#: A path into this repository, as a command line writes one. Only paths
#: under a directory we ship are checked: `notes.md` and `deploy.yaml` in an
#: example are the reader's files, not ours.
REPOSITORY_PATH = re.compile(r"^(?:specs|docs|scripts|phabfive|tests)/\S+$")


#: Every file whose fenced blocks are read as instructions: the agent skill
#: first, then the pages and the readme.
PROSE_FILES = [
    SKILL,
    REPO_ROOT / "README.md",
    *sorted((REPO_ROOT / "docs").glob("*.md")),
]


def _command_lines(path):
    """Every line inside a fenced block of one file that invokes `phabfive`.

    A line is taken as written apart from a trailing backslash: a shell
    continuation joins two lines of one command, and the pipeline, the
    process substitution and the `jq` beside it are split off below. A
    leading `$ ` is stripped, because a `console` block writes the prompt and
    the output it produced in the same fence - and a line of output that
    happens to hold the word `phabfive` is not an invocation, so only a line
    that *starts* with it, or with the prompt before it, is taken.
    """
    lines = []
    fenced = False

    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue

        if not fenced:
            continue

        stripped = line.strip()

        if stripped.startswith("$ "):
            stripped = stripped[2:].lstrip()

        if not stripped.startswith("phabfive") and "| phabfive" not in stripped:
            continue

        lines.append((path, number, stripped.rstrip().rstrip("\\").strip()))

    return lines


COMMAND_LINES = [case for path in PROSE_FILES for case in _command_lines(path)]


def _invocations(line):
    """The `phabfive ...` invocations one line holds, as token lists.

    A line may be a pipeline, or hold a process substitution: what is
    extracted is every run of tokens that starts with `phabfive` and ends at
    the next shell operator.
    """
    cleaned = line.replace("<(", " ").replace(")", " ").replace("|", " | ")

    try:
        tokens = shlex.split(cleaned, comments=True)
    except ValueError:
        return []

    found = []
    current = None

    for token in tokens:
        if token == "phabfive":
            current = []
            found.append(current)
        elif token in ("|", "&&", ";", ">", "<"):
            current = None
        elif current is not None:
            current.append(token)

    return found


def _ids(case):
    path, number, line = case
    return f"{path.name}:{number}:{line[:50]}"


def _where(path, number):
    return f"{path.relative_to(REPO_ROOT)}:{number}"


def test_the_skill_has_command_lines():
    """Guard the guard: an empty walk would pass every case below."""
    assert SKILL.is_file()
    assert len(COMMAND_LINES) > 50

    named = {path for path, _, _ in COMMAND_LINES}

    assert SKILL in named
    assert REPO_ROOT / "docs" / "maniphest-cli.md" in named


@pytest.mark.parametrize("case", COMMAND_LINES, ids=_ids)
def test_a_documented_command_line_resolves(case):
    """The command path, the options on it, and the files it names."""
    path, number, line = case
    where = _where(path, number)
    root = typer.main.get_command(app)

    for tokens in _invocations(line):
        command = root
        walked = ["phabfive"]
        seen_argument = False
        skip = False

        for token in tokens:
            if skip:
                # The value of the option before it, not a subcommand and not
                # a path of ours: `--install-completion fish` names a shell.
                skip = False
                continue

            if token.startswith("-") and token != "-":
                spelling, equals, _ = token.partition("=")
                parameter = _declares(command, root, spelling)

                assert parameter is not None, (
                    f"{where}: {' '.join(walked)} has no {spelling}"
                )

                skip = not equals and _takes_a_value(parameter)
                continue

            if seen_argument:
                _check_path(token, where)
                continue

            children = getattr(command, "commands", None)

            if children and token in children:
                command = children[token]
                walked.append(token)
                continue

            if children and MONOGRAM.match(token):
                # `phabfive T123` is the monogram shortcut, which the root
                # group resolves to `maniphest show T123`
                seen_argument = True
                continue

            assert not children or not _looks_like_a_command(token), (
                f"{where}: {' '.join(walked)} has no subcommand {token}"
            )

            seen_argument = True
            _check_path(token, where)


def _looks_like_a_command(token):
    """Whether a bare word could be meant as a subcommand.

    A group takes no argument of its own, so a word after one is either a
    subcommand or a mistake - unless it is a placeholder or a monogram, both
    of which are the reader's value to fill in.
    """
    if PLACEHOLDER.match(token) or MONOGRAM.match(token):
        return False

    return bool(re.fullmatch(r"[a-z][a-z-]*", token))


#: click adds the help option itself rather than declaring it as a
#: parameter, so it is named here rather than looked for.
BUILT_IN_OPTIONS = {"--help", "-h"}


#: What `--help` resolves to, since click adds it rather than declaring it.
#: A truthy sentinel that takes no value, so the walk treats it as a flag.
_HELP = object()


def _declares(command, root, spelling):
    """The parameter this command or the root group declares, or None.

    The root carries the global options - `--format`, `-v` - and they are
    written before the subcommand, so both are consulted. The parameter
    itself is returned rather than a boolean, because whether it takes a
    value decides whether the next token is a value or a subcommand.
    """
    if spelling in BUILT_IN_OPTIONS:
        return _HELP

    for one in (command, root):
        for parameter in one.params:
            if spelling in parameter.opts or spelling in parameter.secondary_opts:
                return parameter

    return None


def _takes_a_value(parameter):
    """Whether the next token belongs to this option rather than the line.

    A flag does not take one, and neither does a counted option: `-v` is
    `count=True` with `nargs=1`, so asking `nargs` alone would swallow the
    subcommand after it.
    """
    if parameter is _HELP:
        return False

    if getattr(parameter, "is_flag", False) or getattr(parameter, "count", False):
        return False

    return bool(getattr(parameter, "nargs", 1))


def _check_path(token, where):
    """A path into this repository has to be a file that is there."""
    if not REPOSITORY_PATH.match(token):
        return

    assert (REPO_ROOT / token).is_file(), (
        f"{where}: names {token}, which does not exist"
    )


#: A fenced block and the word after the backticks, so a `yaml` block can be
#: told from a `bash` or a `json` one.
FENCED = re.compile(r"^```([a-z]*)\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _spec_examples():
    """Every fenced YAML block in the skill that is a whole spec.

    Recognised by the envelope it opens with rather than by an annotation:
    an example becomes checked by being complete, so a fragment illustrating
    one key is skipped and nothing has to remember to mark it.
    """
    text = SKILL.read_text(encoding="utf-8")

    return [
        body
        for language, body in FENCED.findall(text)
        if language == "yaml" and body.startswith("spec: phorge/")
    ]


SPEC_EXAMPLES = _spec_examples()


def test_the_skill_shows_a_spec():
    """Guard the guard: no examples would make the case below vacuous."""
    assert SPEC_EXAMPLES


@pytest.mark.parametrize("body", SPEC_EXAMPLES, ids=range(len(SPEC_EXAMPLES)))
def test_a_spec_example_validates_offline(body):
    """An agent copies this block, so it has to be a spec that loads.

    The offline layer only: resolving the names in it would need an
    instance, and the point is the shape.
    """
    from phabfive.spec import parse_spec, validate_offline

    problems = validate_offline(parse_spec(body, format="yaml"))

    assert problems == [], "\n".join(
        f"{one.severity}: {one.object}: {one.reason} [{one.code}]" for one in problems
    )
