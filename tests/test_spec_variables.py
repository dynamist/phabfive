# -*- coding: utf-8 -*-

"""The variable engine, now that it belongs to every spec kind (#471).

`phabfive/maniphest/utils.py`'s five engine functions moved here, so the
classes that cover them stay where they are in `tests/test_maniphest.py`
against the re-export aliases. What this file covers is what is new: the
module's independence from Maniphest, `StrictUndefined`, declared defaults,
and caller-supplied overrides.
"""

import subprocess
import sys

import pytest

from phabfive.exceptions import PhabfiveDataException
from phabfive.spec.variables import (
    declared_value,
    is_declaration,
    missing_variables,
    render_string,
    render_tree,
    render_variables_with_dependency_resolution,
    resolve_variables,
    undefined_names,
)


class TestItLeftManiphestBehind:
    """The point of the move: the engine no longer belongs to one app."""

    def test_importing_it_does_not_import_maniphest(self):
        # In process this proves nothing - by the time the suite runs,
        # phabfive.maniphest is already in sys.modules - so ask a fresh
        # interpreter, the same technique tests/test_public_api.py uses
        code = (
            "import sys; import phabfive.spec.variables; "
            "print([m for m in sys.modules "
            "if m.startswith('phabfive.maniphest') "
            "or m in ('phabricator', 'requests', 'phabfive.cli', "
            "'phabfive.core', 'phabfive.conduit')])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == "[]", result.stdout

    def test_the_old_names_still_answer_from_maniphest_utils(self):
        """A consumer importing them from the old module is not broken."""
        from phabfive.maniphest import utils
        from phabfive.spec import variables

        for name in (
            "build_dependency_graph",
            "detect_circular_dependencies",
            "extract_variable_dependencies",
            "render_variables_with_dependency_resolution",
            "topological_sort",
        ):
            assert getattr(utils, name) is getattr(variables, name)


class TestEverySpecKindGetsVariables:
    """Search specs have no variables today; the engine does not care."""

    def test_a_search_body_renders(self):
        variables = resolve_variables({"team": "Backend", "sprint": 42})
        body = {
            "searches": [
                {
                    "type": "task",
                    "title": "{{ team }} sprint {{ sprint }}",
                    "search": {
                        "tag": ["{{ team }}"],
                        "status": "open",
                        "limit": 20,
                    },
                }
            ]
        }

        rendered = render_tree(body, variables)

        assert rendered["searches"][0]["title"] == "Backend sprint 42"
        assert rendered["searches"][0]["search"]["tag"] == ["Backend"]
        # Untouched: a non-string is not a template
        assert rendered["searches"][0]["search"]["limit"] == 20

    def test_render_tree_does_not_mutate_its_argument(self):
        body = {"searches": [{"title": "{{ team }}"}]}

        render_tree(body, {"team": "Backend"})

        assert body == {"searches": [{"title": "{{ team }}"}]}

    def test_render_tree_leaves_mapping_keys_alone(self):
        """Keys are spec keys, not user text."""
        rendered = render_tree({"{{ team }}": "{{ team }}"}, {"team": "Backend"})

        assert rendered == {"{{ team }}": "Backend"}


class TestAnUndefinedVariableIsAnError:
    """The behaviour change: Jinja2's Undefined rendered as the empty string."""

    def test_the_old_spelling_fails_loudly(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            render_string("Sprint {{ sprint_numbr }} planning", {"sprint": 42})

        # It names itself, which is the whole point - "Sprint  planning" did
        # not, and the task was created with the wrong title
        assert "sprint_numbr" in str(excinfo.value)

    def test_it_names_the_default_filter_as_the_way_out(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            render_string("{{ sprint_numbr }}", {})

        assert "default" in str(excinfo.value)

    def test_the_default_filter_still_renders(self):
        assert render_string('{{ sprint | default("42") }}', {}) == "42"

    def test_a_supplied_value_beats_the_default_filter(self):
        assert render_string('{{ sprint | default("42") }}', {"sprint": 43}) == "43"

    def test_a_nested_body_names_the_undefined_variable(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            render_tree({"tasks": [{"title": "Sprint {{ sprint_numbr }}"}]}, {})

        message = str(excinfo.value)
        assert "sprint_numbr" in message
        # And where it was, so a 200-task spec is navigable
        assert "tasks[0].title" in message

    def test_a_variable_naming_an_undefined_variable_fails(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            render_variables_with_dependency_resolution({"greeting": "Hi {{ nmae }}"})

        assert "nmae" in str(excinfo.value)
        assert "greeting" in str(excinfo.value)

    def test_an_undefined_attribute_of_a_defined_variable_is_reported(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            render_string("{{ team.nosuch }}", {"team": "Backend"})

        assert "nosuch" in str(excinfo.value)

    def test_undefined_names_lists_them_without_rendering(self):
        assert undefined_names("{{ a }} {{ b }} {{ c }}", {"b": 1}) == ["a", "c"]

    def test_a_broken_template_is_refused_rather_than_traced_back(self):
        with pytest.raises(PhabfiveDataException):
            render_string("{% for x in %}", {})


class TestDeclaredDefaults:
    """`variables: {sprint: {default: 42}}` makes a variable optional."""

    def test_a_declared_default_is_used_when_nothing_overrides_it(self):
        assert resolve_variables({"sprint": {"default": 42}}) == {"sprint": 42}

    def test_an_override_beats_a_declared_default(self):
        resolved = resolve_variables({"sprint": {"default": 42}}, {"sprint": 43})

        assert resolved == {"sprint": 43}

    def test_an_override_beats_a_plain_value(self):
        resolved = resolve_variables({"sprint": 42}, {"sprint": 43})

        assert resolved == {"sprint": 43}

    def test_an_override_for_an_undeclared_variable_is_kept(self):
        """A spec may read {{ sprint }} and leave supplying it to the caller."""
        resolved = resolve_variables(None, {"sprint": 43})

        assert resolved == {"sprint": 43}
        assert render_string("Sprint {{ sprint }}", resolved) == "Sprint 43"

    def test_a_variable_with_neither_value_nor_default_is_an_error(self):
        # `sprint:` with nothing after it, which YAML reads as None
        with pytest.raises(PhabfiveDataException) as excinfo:
            resolve_variables({"sprint": None})

        assert "sprint" in str(excinfo.value)

    def test_it_is_not_an_error_once_supplied(self):
        assert resolve_variables({"sprint": None}, {"sprint": 43}) == {"sprint": 43}

    def test_missing_variables_names_every_one_of_them(self):
        assert missing_variables({"a": None, "b": 1, "c": None}, {"c": 3}) == ["a"]

    def test_a_default_is_rendered_like_any_other_value(self):
        resolved = resolve_variables(
            {"team": "Backend", "board": {"default": "{{ team }} board"}}
        )

        assert resolved["board"] == "Backend board"

    def test_variables_still_resolve_in_dependency_order(self):
        resolved = resolve_variables(
            {
                "greeting": "Hello {{ name }}",
                "name": {"default": "{{ title }} Alice"},
                "title": "Dr",
            }
        )

        assert resolved["greeting"] == "Hello Dr Alice"

    def test_a_cycle_is_still_refused(self):
        with pytest.raises(PhabfiveDataException, match="Circular reference"):
            resolve_variables({"a": "{{ b }}", "b": {"default": "{{ a }}"}})


class TestAMappingValueIsStillAValue:
    """The disambiguation: {default: x} declares, any other mapping is a value."""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param({"lead": "alice"}, id="mapping"),
            pytest.param({}, id="empty-mapping"),
            pytest.param({"default": 1, "description": "x"}, id="extra-key"),
            pytest.param(["hholm", "grok"], id="list"),
            pytest.param("Backend", id="string"),
            pytest.param(42, id="int"),
        ],
    )
    def test_it_is_not_a_declaration(self, value):
        assert is_declaration(value) is False
        assert declared_value(value) == (True, value)

    def test_only_a_lone_default_key_declares(self):
        assert is_declaration({"default": 42}) is True
        assert declared_value({"default": 42}) == (True, 42)

    def test_a_mapping_valued_variable_survives_resolution(self):
        """templates/task-create/test-template-v2.yml has a list-valued one."""
        resolved = resolve_variables(
            {"workgroup": ["hholm", "grok"], "owners": {"lead": "alice"}}
        )

        assert resolved["workgroup"] == ["hholm", "grok"]
        assert resolved["owners"] == {"lead": "alice"}

    def test_an_explicit_null_default_is_a_value_not_a_hole(self):
        assert declared_value({"default": None}) == (True, None)
        assert resolve_variables({"sprint": {"default": None}}) == {"sprint": None}


class TestACreateTemplateSeesDeclaredDefaults:
    """`maniphest create --with` resolves variables through the same engine.

    The behaviour a create template gains from the move: `{default: 42}` is a
    declaration rather than a mapping value, so `{{ sprint }}` renders `42`
    instead of `{'default': 42}`. The command has no `--set` yet (#474), so
    nothing overrides a default here; what is pinned is that the declaration
    is understood at all.
    """

    @staticmethod
    def _create(config):
        from unittest.mock import MagicMock, patch

        from phabfive.maniphest.core import Maniphest

        phab = MagicMock()
        phab.project.query.return_value = {"data": {}}
        ids = iter(range(1, 100))
        phab.maniphest.edit.side_effect = lambda transactions: {
            "object": {"id": next(ids), "phid": "PHID-TASK-1"}
        }

        with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
            maniphest = Maniphest()

        maniphest.phab = phab
        maniphest.url = "http://phorge.localhost"
        maniphest.conf = {}
        maniphest.create_tasks_from_config(config)

        return phab.maniphest.edit.call_args_list[0].kwargs["transactions"]

    def test_a_declared_default_renders_as_its_value(self):
        transactions = self._create(
            {
                "variables": {"sprint": {"default": 42}},
                "tasks": [{"title": "Sprint {{ sprint }}", "description": "x"}],
            }
        )

        assert {"type": "title", "value": "Sprint 42"} in transactions

    def test_a_mapping_valued_variable_is_still_a_mapping(self):
        transactions = self._create(
            {
                "variables": {"owners": {"lead": "alice"}},
                "tasks": [{"title": "{{ owners.lead }}", "description": "x"}],
            }
        )

        assert {"type": "title", "value": "alice"} in transactions

    def test_a_variable_with_no_value_names_itself(self):
        with pytest.raises(PhabfiveDataException, match="sprint"):
            self._create(
                {
                    "variables": {"sprint": None},
                    "tasks": [{"title": "Sprint {{ sprint }}", "description": "x"}],
                }
            )
