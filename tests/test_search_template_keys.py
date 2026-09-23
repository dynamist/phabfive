# -*- coding: utf-8 -*-
"""The keys a search template may use are exactly the keys the CLI reads.

_load_search_config refuses any key not in SEARCH_TEMPLATE_KEYS, which is now
derived from phabfive.spec.registry. The CLI reads each template key with
get_param(..., yaml_params, "key") and offers the same value as a flag. The
two once drifted apart - created-before, updated-before, space, limit,
show-policy and all were documented and read, yet a template using them failed
to load (#295).

This used to be guarded by a regex over phabfive/cli/maniphest.py's source,
which is what you write when there is no single declaration to compare
against. There is one now, so the parity check compares declarations: every
field claiming a flag has one, and every flag on the command is a declared
field.
"""

from pathlib import Path

import pytest
from typer.main import get_command

from phabfive.cli.maniphest import maniphest_app
from phabfive.constants import SEARCH_TEMPLATE_KEYS
from phabfive.maniphest import Maniphest
from phabfive.spec.registry import FieldKind, cli_flags, fields_for, spec_keys

# Flags on "maniphest search" that are deliberately not spec keys.
# --with names the template file itself, so it cannot be a key inside one,
# and --help is click's own. Every other flag must be a declared field.
EXEMPT = frozenset({"--with", "--help"})

# One plausible value per kind, as the YAML scalar is written - @me needs its
# quotes, since "@" cannot start a plain scalar. _load_search_config only
# checks that the key is accepted, but a template full of "x" would not say
# what a key actually looks like.
SAMPLE_VALUES = {
    FieldKind.TEXT: "needle",
    FieldKind.INT: "10",
    FieldKind.BOOL: "true",
    FieldKind.TIME: "1y",
    FieldKind.ENUM: "any",
    FieldKind.ORDER: "updated:desc",
    FieldKind.PATTERN: "any",
    FieldKind.USER: '"@me"',
    FieldKind.PROJECT: "myproject",
    FieldKind.SPACE: "S1",
    FieldKind.POLICY: "users",
    FieldKind.MONOGRAM: "T1",
    FieldKind.INSTANCE_ENUM: "high",
}


def _declared_flags():
    """Every long flag "maniphest search" actually offers."""
    command = get_command(maniphest_app).commands["search"]
    return {
        opt for param in command.params for opt in param.opts if opt.startswith("--")
    }


def test_the_command_has_flags():
    # Guards the two assertions below it: an empty set would pass the first
    # and would make the second fail for the wrong reason.
    assert {"--tag", "--status", "--created-before"} <= _declared_flags()


def test_every_declared_flag_exists_on_the_command():
    assert cli_flags("task", "search") <= _declared_flags()


def test_every_flag_on_the_command_is_a_declared_field():
    assert _declared_flags() - EXEMPT == cli_flags("task", "search")


# The 22 key spellings every shipped template and every documented example
# is written with, listed once, by hand, on purpose. SEARCH_TEMPLATE_KEYS is
# now *derived* from the registry, so comparing the two is a tautology and
# pins nothing: rename a Field's `name` and leave its `cli` alone and the
# accepted set changes silently, breaking every template in the wild, while
# the parity tests above still pass because they compare `cli`. This is the
# literal that says no.
#
# Adding a key here is a new key, which is fine. Changing one is a rename,
# which needs an alias and a deprecation, not an edit to this list.
HISTORIC_KEYS = frozenset(
    {
        "all",
        "assigned",
        "author",
        "column",
        "created-after",
        "created-before",
        "editable-by",
        "exclude",
        "include",
        "limit",
        "order",
        "priority",
        "show-history",
        "show-metadata",
        "show-policy",
        "space",
        "status",
        "tag",
        "text_query",
        "updated-after",
        "updated-before",
        "visible-to",
    }
)


def test_the_accepted_keys_are_the_declared_ones():
    assert set(SEARCH_TEMPLATE_KEYS) == set(spec_keys("task", "search"))


def test_the_declared_keys_are_still_spelled_the_way_templates_write_them():
    """One hyphen turned into camelCase breaks every template ever written.

    `text_query` is the one key with an underscore and it stays that way in
    Phase 1; an alias mechanism is Phase 2's problem.
    """
    assert set(spec_keys("task", "search")) == HISTORIC_KEYS


@pytest.mark.parametrize(
    "field",
    fields_for("task", "search"),
    ids=lambda field: field.name,
)
def test_template_with_key_loads(tmp_path, field):
    """Every declared field is accepted by the loader, not just some of them.

    This is #295 closed structurally: the list that used to be hand-written
    here is now the registry itself, so a key cannot be declared and refused.
    """
    template = tmp_path / "search.yaml"
    template.write_text(f"search:\n  {field.name}: {SAMPLE_VALUES[field.kind]}\n")
    maniphest = Maniphest.__new__(Maniphest)

    configs = maniphest._load_search_config(str(template))

    assert field.name in configs[0]["search"]


def _documented_keys():
    """The keys docs/search-templates.md lists, read off its bullet list.

    The "Supported Parameters" section is the reference a template author
    reads. A key declared in the registry, accepted by the loader and
    offered as a flag is still undiscoverable if nothing writes it down -
    which is the fourth leg of #470's "one declaration, four consequences".
    """
    import re

    text = (
        Path(__file__).resolve().parent.parent / "docs" / "search-templates.md"
    ).read_text(encoding="utf-8")
    section = text.split("## Supported Parameters", 1)[1].split(
        "**Time Unit Support:**", 1
    )[0]

    keys = set()
    for line in section.splitlines():
        match = re.match(r"^- (`[^`]+`(?:, `[^`]+`)*):", line)
        if match:
            keys |= set(re.findall(r"`([^`]+)`", match.group(1)))

    return keys


def test_the_documentation_lists_every_declared_key():
    """A key nobody can find out about might as well not be declared."""
    documented = _documented_keys()

    assert documented, "the Supported Parameters list was not found"
    assert documented == set(spec_keys("task", "search"))
