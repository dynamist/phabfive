# -*- coding: utf-8 -*-

"""The one problem record both validation layers report through (#468).

`code` is the stable slug a CI job and a web frontend branch on, so the
vocabulary is a real module constant rather than a docstring: eight codes
had already been added without the docstring list growing by the time this
file was written, which is exactly the drift a list nothing reads invites.

The test that matters is the last one: every `code=` written anywhere under
`phabfive/spec/` or in `phabfive/cli/spec.py` is a documented code. It reads
the source with `ast` rather than exercising every check, because a check
nobody reached is still a code a frontend will one day see.
"""

import ast
import json
from pathlib import Path

import pytest

from phabfive.spec.problems import CODES, Layer, Problem, Severity, problem

REPOSITORY = Path(__file__).resolve().parent.parent
SOURCES = sorted((REPOSITORY / "phabfive" / "spec").glob("*.py")) + [
    REPOSITORY / "phabfive" / "cli" / "spec.py"
]


def _emitted_codes(path):
    """Every literal passed as `code=` or `problem=` in one module.

    `code=` is the Problem field; `problem=` is what a `ResolveResult`
    carries, which `validate_online` copies straight into `Problem.code`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg in ("code", "problem") and isinstance(
                keyword.value, ast.Constant
            ):
                if isinstance(keyword.value.value, str):
                    found.add(keyword.value.value)

    return found


class TestTheRecord:
    def test_it_compares_by_value(self):
        assert problem("tasks[0]", reason="x", code="unknown-key") == Problem(
            object="tasks[0]",
            field=None,
            value=None,
            reason="x",
            code="unknown-key",
        )

    def test_the_defaults_are_an_offline_error(self):
        one = problem("$", reason="x", code="unknown-key")

        assert one.layer == Layer.OFFLINE
        assert one.severity == Severity.ERROR

    def test_the_record_survives_json(self):
        one = problem(
            "tasks[0]", field="limit", value=object(), reason="x", code="wrong-type"
        )

        assert json.loads(json.dumps(one.as_record(), default=str))["code"] == (
            "wrong-type"
        )

    def test_the_record_carries_exactly_the_documented_keys(self):
        record = problem("$", reason="x", code="unknown-key").as_record()

        assert sorted(record) == [
            "code",
            "field",
            "layer",
            "object",
            "reason",
            "severity",
            "value",
        ]


class TestTheVocabulary:
    def test_no_slug_is_listed_twice(self):
        assert len(CODES) == len(set(CODES))

    def test_every_slug_is_kebab_case(self):
        for code in CODES:
            assert code == code.lower()
            assert " " not in code and "_" not in code

    def test_the_sources_are_there(self):
        """Guard the guard: an empty glob would make the next test vacuous."""
        assert len(SOURCES) >= 10

    @pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
    def test_every_code_a_module_emits_is_a_documented_one(self, path):
        undocumented = sorted(_emitted_codes(path) - set(CODES))

        assert undocumented == [], (
            f"{path.name} emits codes that phabfive/spec/problems.py does not "
            f"list: {undocumented}"
        )

    def test_the_test_would_see_an_undocumented_code(self, tmp_path):
        """The check above is a source walk, so prove the walk sees one."""
        path = tmp_path / "fake.py"
        path.write_text(
            "problem('$', reason='x', code='invented-here')\n", encoding="utf-8"
        )

        assert _emitted_codes(path) == {"invented-here"}
