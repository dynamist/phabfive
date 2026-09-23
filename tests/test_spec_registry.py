# -*- coding: utf-8 -*-
"""The field registry is the one declaration everything else derives from."""

import os
import subprocess
import sys
import textwrap

import pytest

from phabfive.exceptions import PhabfiveInputException
from phabfive.spec import registry
from phabfive.spec.registry import (
    FIELDS,
    Field,
    FieldKind,
    cli_flags,
    constraint_for,
    field_by_name,
    fields_for,
    spec_keys,
)


def _clean_env():
    """The environment minus what another test module already set.

    Importing phabfive.cli sets TYPER_USE_RICH in this process, and a child
    inherits it, so a subprocess asserting that nothing set it has to start
    from an environment where nothing has.
    """
    return {key: value for key, value in os.environ.items() if key != "TYPER_USE_RICH"}


class TestDeclarations:
    def test_phase_one_declares_the_keys_that_exist_today(self):
        # Not one more: a field declared here but not read by the command
        # would be accepted by the loader and silently ignored, which is the
        # drift the registry exists to end.
        assert len(fields_for("task", "search")) == 22

    def test_every_field_is_declared_once(self):
        names = [field.name for field in FIELDS]
        assert len(names) == len(set(names))

    def test_text_query_keeps_its_underscore(self):
        # The one key with an underscore while every other key is hyphenated.
        # Phase 1 describes the quirk rather than fixing it.
        underscored = {field.name for field in FIELDS if "_" in field.name}
        assert underscored == {"text_query"}

    def test_only_enum_fields_carry_choices(self):
        # Priority, status, space and policy names are what the instance says
        # they are, so a choices list here would bless a value the server
        # never named.
        assert [
            field.name
            for field in FIELDS
            if field.choices and field.kind is not FieldKind.ENUM
        ] == []

    def test_client_side_fields_declare_no_constraint(self):
        # --column, --priority and the policy filters are applied in Python,
        # in transitions/ and maniphest/core.py, and never sent.
        for name in ("column", "priority", "visible-to", "editable-by"):
            field = field_by_name(name, "task", "search")
            assert field is not None
            assert constraint_for(field, "task") is None

    def test_server_side_fields_name_their_constraint(self):
        sent = {
            field.name: constraint_for(field, "task")
            for field in fields_for("task", "search")
            if constraint_for(field, "task") is not None
        }
        assert sent == {
            "text_query": "query",
            "tag": "projects",
            "assigned": "assigned",
            "author": "authorPHIDs",
            "space": "spaces",
            "created-after": "createdStart",
            "created-before": "createdEnd",
            "updated-after": "modifiedStart",
            "updated-before": "modifiedEnd",
            "status": "statuses",
        }

    def test_the_author_constraint_is_named_per_application(self):
        # maniphest.search says authorPHIDs, paste.search says authors, and a
        # wrong key fails with ERR-INVALID-CONSTRAINT.
        author = field_by_name("author", "task", "search")
        assert author is not None
        assert constraint_for(author, "task") == "authorPHIDs"
        assert constraint_for(author, "paste") == "authors"

    def test_all_is_declared_deprecated(self):
        deprecated = {
            field.name: field.deprecated for field in FIELDS if field.deprecated
        }
        assert deprecated == {"all": "status: any"}

    def test_text_query_has_no_flag(self):
        # It is a positional argument on the command, not an option.
        text_query = field_by_name("text_query", "task", "search")
        assert text_query is not None
        assert text_query.cli is None
        assert "--text-query" not in cli_flags("task", "search")

    def test_a_field_is_frozen(self):
        field = FIELDS[0]
        with pytest.raises(Exception):
            field.name = "nope"


class TestAccessors:
    def test_fields_for_keeps_declaration_order(self):
        names = [field.name for field in fields_for("task", "search")]
        assert names == [field.name for field in FIELDS if field.name in names]
        assert names[0] == "text_query"

    def test_an_object_type_with_no_fields_yet_is_empty_not_an_error(self):
        assert fields_for("paste", "search") == ()
        assert spec_keys("task", "create") == frozenset()

    def test_field_by_name_answers_none_for_an_undeclared_key(self):
        assert field_by_name("no-such-key", "task", "search") is None

    def test_an_unknown_object_type_is_an_input_error(self):
        # A caller asking for one is a programming error, not user data.
        with pytest.raises(PhabfiveInputException) as excinfo:
            fields_for("widget", "search")
        assert "widget" in str(excinfo.value)

    def test_an_unknown_verb_is_an_input_error(self):
        with pytest.raises(PhabfiveInputException) as excinfo:
            spec_keys("task", "destroy")
        assert "destroy" in str(excinfo.value)

    def test_cli_flags_are_the_flags_of_the_fields_that_have_one(self):
        flags = cli_flags("task", "search")
        assert "--created-after" in flags
        assert all(flag.startswith("--") for flag in flags)
        assert len(flags) == len(fields_for("task", "search")) - 1  # text_query


class TestDerivation:
    def test_adding_a_field_changes_the_derivation(self, monkeypatch):
        extra = Field(
            name="milestone",
            kind=FieldKind.PROJECT,
            objects=frozenset({"task"}),
            verbs=frozenset({"search"}),
            cli="--milestone",
            constraint="parentIDs",
        )
        monkeypatch.setattr(registry, "FIELDS", FIELDS + (extra,))

        assert "milestone" in spec_keys("task", "search")
        assert "--milestone" in cli_flags("task", "search")
        assert field_by_name("milestone", "task", "search") is extra

    def test_a_field_added_to_the_registry_is_accepted_by_the_loader(
        self, tmp_path, monkeypatch
    ):
        """One declaration is all it takes, end to end.

        In process, because `_load_search_config` calls `spec_keys()` when it
        runs rather than reading a set derived once at import. That is the
        whole point of dropping the derived constant: a new field is a new
        declaration and nothing else, with no import order to get right.
        """
        from phabfive.maniphest import Maniphest

        extra = Field(
            name="milestone",
            kind=FieldKind.PROJECT,
            objects=frozenset({"task"}),
            verbs=frozenset({"search"}),
            cli="--milestone",
            constraint="parentIDs",
        )
        monkeypatch.setattr(registry, "FIELDS", FIELDS + (extra,))

        template = tmp_path / "search.yaml"
        template.write_text("search:\n  milestone: Q3\n")

        configs = Maniphest.__new__(Maniphest)._load_search_config(str(template))

        assert configs[0]["search"]["milestone"] == "Q3"

    def test_an_undeclared_key_is_still_refused_by_the_loader(self, tmp_path):
        from phabfive.exceptions import PhabfiveDataException
        from phabfive.maniphest import Maniphest

        template = tmp_path / "search.yaml"
        template.write_text("search:\n  milestone: Q3\n")

        with pytest.raises(PhabfiveDataException) as excinfo:
            Maniphest.__new__(Maniphest)._load_search_config(str(template))

        assert "milestone" in str(excinfo.value)


class TestImportHygiene:
    """The registry is the cheap, dependency-free bottom of the spec stack."""

    def test_the_registry_imports_only_the_standard_library(self):
        """phabfive.constants imports from it, so anything heavier is a cycle.

        Out of process, and loaded straight from its file so that the
        subpackage's __init__ is not what is being measured: by the time this
        suite runs, ruamel, jinja2 and requests are all in sys.modules
        whatever this one module imports.
        """
        code = textwrap.dedent(
            """
            import importlib.util
            import pathlib
            import sys

            import phabfive

            path = pathlib.Path(phabfive.__file__).parent / "spec" / "registry.py"
            spec = importlib.util.spec_from_file_location("_registry", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            assert module.spec_keys("task", "search")

            forbidden = [
                name
                for name in (
                    "ruamel",
                    "ruamel.yaml",
                    "jinja2",
                    "requests",
                    "phabricator",
                    "typer",
                    "click",
                    "rich",
                    "anyconfig",
                    "phabfive.cli",
                    "phabfive.core",
                    "phabfive.conduit",
                    "phabfive.constants",
                )
                if name in sys.modules
            ]
            assert not forbidden, forbidden
            print("ok")
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"

    def test_importing_constants_does_not_reach_the_cli(self):
        """phabfive.constants stays a leaf of plain literals.

        phabfive/cli/__init__.py sets os.environ["TYPER_USE_RICH"] as it
        imports, so a library module that reaches it mutates the environment
        of any program that touched phabfive.constants.
        """
        code = textwrap.dedent(
            """
            import os
            import sys

            import phabfive.constants

            forbidden = [
                name
                for name in ("phabfive.cli", "typer", "click", "phabricator")
                if name in sys.modules
            ]
            assert not forbidden, forbidden
            assert "TYPER_USE_RICH" not in os.environ
            print("ok")
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"

    def test_importing_constants_stays_cheap(self):
        """phabfive.constants must not reach the spec package at all.

        It briefly did, to derive `SEARCH_TEMPLATE_KEYS`, and that one edge
        cost a quarter of a second on a module every CLI path imports -
        reaching phabfive.spec.registry runs phabfive/spec/__init__.py, which
        pulls in ruamel, jinja2 and both validation layers. The set is gone
        and `_load_search_config` calls `spec_keys()` itself, so the edge
        should never come back; this is what says so.

        Out of process, because by the time this suite runs everything is in
        sys.modules already.
        """
        code = textwrap.dedent(
            """
            import sys

            import phabfive.constants

            eager = sorted(
                name
                for name in sys.modules
                if name.split(".")[0] in ("ruamel", "jinja2", "tomli")
                or name.startswith("phabfive.spec")
            )
            assert not eager, eager

            # The literals are all still there; it is only the derived set
            # that left.
            assert phabfive.constants.MANIPHEST_ORDER_DEFAULT
            assert not hasattr(phabfive.constants, "SEARCH_TEMPLATE_KEYS")
            print("ok")
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"
