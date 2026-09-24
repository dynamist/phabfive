# -*- coding: utf-8 -*-
"""The keys a search spec may use are exactly the keys the CLI reads.

_load_search_config refuses any key `spec_keys("task", "search")` does not
name, which the registry answers from one Field declaration per key. The CLI
reads each key with get_param(..., yaml_params, "key") and offers the same
value as a flag. The two once drifted apart - created-before, updated-before,
space, limit, show-policy and all were documented and read, yet a file using
them failed to load (#295).

This used to be guarded by a regex over phabfive/cli/maniphest.py's source,
which is what you write when there is no single declaration to compare
against. There is one now, so the parity check compares declarations: every
field claiming a flag has one, and every flag on the command is a declared
field.

The third leg - that every declared key is also written down where somebody
can find it - used to be here as well, reading a bullet list out of
the old search-template page. It now lives in tests/test_spec_docs.py, against
the generated tables of docs/phorge-spec.md and over all eight (object type,
verb) pairs rather than task/search alone. The key set is one fact, and a
prose list of it was the other half of the drift #295 was.
"""

import pytest
from typer.main import get_command

from phabfive.cli.maniphest import maniphest_app
from phabfive.maniphest import Maniphest
from phabfive.spec.registry import FieldKind, cli_flags, fields_for

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
