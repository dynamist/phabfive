# -*- coding: utf-8 -*-

"""Every `--with` in the tree is the same option, declared once.

`--with` reached seven commands one at a time, and by the end of Phase 4 the
seven declarations had drifted into three different help strings:

* `maniphest create` said "Load task creation template from YAML file" and
  `maniphest search` "Load search parameters from a YAML template file" -
  neither mentioning that the option is deprecated, and both naming YAML
  when the loader reads four serializations;
* `project|paste|passphrase search` said "Load the search from a YAML search
  spec; every option below overrides what the spec says" - the behaviour
  right, the deprecation and the formats missing;
* `project|paste create` said "Deprecated: create everything a create spec
  holds" - the deprecation right, the formats and the behaviour missing.

Two of the seven carried `complete_spec_file`, so TAB offered a `.png` on
the other five. None of that was decided; it was seven copies of one option
drifting apart, which is the same shape as the key lists that drifted in
#295 and the SKILL.md claims that drifted in #491.

So `phabfive.cli.spec_flags.with_spec_option` declares it once and every
call site assigns it, and this file asserts the tree has no second copy: the
set of commands that take `--with` is the expected seven, and each one's
option is byte-identical to what the factory produces for its kind.

Nothing is executed and no instance is asked; this is click's own parameter
list.
"""

import pytest
from typer.main import get_command

from phabfive.cli import app
from phabfive.cli.completers import complete_spec_file
from phabfive.cli.spec_flags import COMMAND_OF_KIND, with_spec_option

#: Every command that takes `--with`, and the kind of spec it runs. A
#: command added to or removed from this option is a change to the
#: deprecation surface and has to be made here on purpose.
EXPECTED = {
    ("maniphest", "create"): "create",
    ("project", "create"): "create",
    ("paste", "create"): "create",
    ("maniphest", "search"): "search",
    ("project", "search"): "search",
    ("paste", "search"): "search",
    ("passphrase", "search"): "search",
}


def _walk(command, path=()):
    """Every (path, click command) leaf under one group."""
    children = getattr(command, "commands", None)

    if not children:
        yield path, command
        return

    for name, child in children.items():
        yield from _walk(child, (*path, name))


def _with_option(command):
    """The `--with` parameter of one command, or None."""
    for parameter in command.params:
        if "--with" in getattr(parameter, "opts", ()):
            return parameter

    return None


ROOT = get_command(app)

FOUND = {
    path: parameter
    for path, command in _walk(ROOT)
    if (parameter := _with_option(command)) is not None
}


def test_exactly_these_commands_take_with():
    """A new `--with` is a deliberate addition to a deprecated surface."""
    assert set(FOUND) == set(EXPECTED)


@pytest.mark.parametrize("path", sorted(EXPECTED), ids=lambda path: " ".join(path))
def test_the_option_is_the_shared_declaration(path):
    """Help and completer come from the factory, not from a local copy."""
    from typer.main import get_click_param
    from typer.models import ParamMeta

    kind = EXPECTED[path]
    wanted, _ = get_click_param(
        ParamMeta(name="with_template", default=with_spec_option(kind), annotation=str)
    )

    assert FOUND[path].help == wanted.help


@pytest.mark.parametrize("path", sorted(EXPECTED), ids=lambda path: " ".join(path))
def test_the_option_completes_spec_files(path):
    """TAB on `--with` offers what the loader reads, like `-f` does.

    `complete_spec_file` filters to the extensions the loader accepts, which
    is the whole reason it exists rather than click's own file completion.
    """
    completer = FOUND[path]._custom_shell_complete

    assert completer is not None, "TAB on this --with offers every file"

    # Typer wraps the callable it was handed in a compatibility shim, and
    # the completer is itself decorated, so the identity check unwraps both:
    # what the shim closed over, down to the function that was written.
    closed_over = [
        getattr(cell.cell_contents, "__wrapped__", cell.cell_contents)
        for cell in completer.__closure__ or ()
    ]

    assert complete_spec_file in closed_over


@pytest.mark.parametrize("path", sorted(EXPECTED), ids=lambda path: " ".join(path))
def test_the_help_names_the_form_that_stays(path):
    """The point of the sentence: what to run instead, spelled as typed."""
    help_text = FOUND[path].help

    assert "Deprecated" in help_text
    assert COMMAND_OF_KIND[EXPECTED[path]] in help_text


@pytest.mark.parametrize("path", sorted(EXPECTED), ids=lambda path: " ".join(path))
def test_the_help_names_every_serialization_the_loader_reads(path):
    """SKILL.md claimed `--with` read a TOML file while ruamel choked on it.

    The four names are in the help because a reader who is told "YAML" will
    not try the other three, and a reader told nothing will try all four.
    """
    help_text = FOUND[path].help

    for serialization in ("YAML", "JSON", "JSONL", "TOML"):
        assert serialization in help_text
