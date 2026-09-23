# -*- coding: utf-8 -*-

"""One loader, four serializations, one internal shape.

The point of phabfive/spec/loader.py is that a spec written as YAML, JSON,
JSONL or TOML parses to the *same* Python objects - so the tests that matter
here are equivalence assertions across the four formats, parametrised rather
than written out four times, and the errors: a spec that does not parse has
to name the file, the format and the position, because "Failed to parse"
alone is what sent #466 to a traceback.
"""

import json

import pytest

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.spec import load_spec, parse_spec
from phabfive.spec.loader import (
    SpecFormat,
    detect_format,
    load_documents,
    parse_documents,
)

# One spec, written four ways. Deliberately exercises every scalar type the
# four formats share - string, int, bool, list, nested mapping - because a
# format that quietly stringifies an int would otherwise pass.
EXPECTED = {
    "spec": "phorge/v1alpha1",
    "kind": "search",
    "metadata": {"name": "stale", "description": "Old high-priority work"},
    "title": "Stale tasks",
    "search": {
        "priority": "high",
        "created-after": 30,
        "show-history": True,
        "tag": ["alpha", "beta"],
    },
}

YAML_TEXT = """\
# A comment, which only YAML and TOML can carry
spec: phorge/v1alpha1
kind: search
metadata:
  name: stale
  description: Old high-priority work
title: Stale tasks
search:
  priority: high
  created-after: 30
  show-history: true
  tag:
    - alpha
    - beta
"""

JSON_TEXT = json.dumps(EXPECTED, indent=2)

JSONL_TEXT = json.dumps(EXPECTED) + "\n"

TOML_TEXT = """\
# A comment, which only YAML and TOML can carry
spec = "phorge/v1alpha1"
kind = "search"
title = "Stale tasks"

[metadata]
name = "stale"
description = "Old high-priority work"

[search]
priority = "high"
created-after = 30
show-history = true
tag = ["alpha", "beta"]
"""

ONE_SPEC = {
    "yaml": YAML_TEXT,
    "json": JSON_TEXT,
    "jsonl": JSONL_TEXT,
    "toml": TOML_TEXT,
}

# The same three documents, in the three formats that can hold several. TOML
# has no multi-document form; its list keys are how it says this, and folding
# a "---" file into those keys is phabfive.spec.envelope's job (#469).
THREE = [
    {"title": "One", "search": {"priority": "high"}},
    {"title": "Two", "search": {"priority": "low"}},
    {"title": "Three", "search": {"priority": "wish"}},
]

MULTI = {
    "yaml": (
        "title: One\nsearch:\n  priority: high\n"
        "---\n"
        "title: Two\nsearch:\n  priority: low\n"
        "---\n"
        "title: Three\nsearch:\n  priority: wish\n"
    ),
    "json": json.dumps(THREE),
    "jsonl": "\n".join(json.dumps(document) for document in THREE) + "\n",
}

SUFFIXES = {"yaml": ".yaml", "json": ".json", "jsonl": ".jsonl", "toml": ".toml"}


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestOneShape:
    """The same spec in four formats parses to identical objects."""

    @pytest.mark.parametrize("fmt", sorted(ONE_SPEC))
    def test_parse_documents_agrees_across_formats(self, fmt):
        assert parse_documents(ONE_SPEC[fmt], format=fmt) == [EXPECTED]

    @pytest.mark.parametrize("fmt", sorted(ONE_SPEC))
    def test_load_documents_agrees_across_formats(self, tmp_path, fmt):
        path = write(tmp_path, f"spec{SUFFIXES[fmt]}", ONE_SPEC[fmt])

        assert load_documents(path) == [EXPECTED]

    @pytest.mark.parametrize("fmt", sorted(ONE_SPEC))
    def test_the_objects_are_plain_python(self, fmt):
        """Not CommentedMap, not a quote-preserving str subclass.

        Equality alone would not catch it - ruamel's containers compare
        equal to the builtins - and a spec that is not json.dumps-able is
        not one a web frontend can hand back.
        """
        document = parse_documents(ONE_SPEC[fmt], format=fmt)[0]

        assert type(document) is dict
        assert type(document["search"]["tag"]) is list
        assert type(document["title"]) is str
        assert json.loads(json.dumps(document)) == EXPECTED


class TestSeveralDocuments:
    """Every format that can hold several documents yields the same list."""

    @pytest.mark.parametrize("fmt", sorted(MULTI))
    def test_multi_document_matches_the_yaml_separator_form(self, fmt):
        assert parse_documents(MULTI[fmt], format=fmt) == THREE
        assert parse_documents(MULTI["yaml"], format="yaml") == THREE

    @pytest.mark.parametrize("fmt", sorted(MULTI))
    def test_multi_document_from_a_file(self, tmp_path, fmt):
        path = write(tmp_path, f"many{SUFFIXES[fmt]}", MULTI[fmt])

        assert load_documents(path) == THREE

    def test_an_empty_yaml_document_is_skipped_not_refused(self):
        """A file ending in "---" is a file, not a broken spec."""
        assert parse_documents("title: One\n---\n", format="yaml") == [{"title": "One"}]

    def test_a_blank_jsonl_line_is_skipped(self):
        text = json.dumps(THREE[0]) + "\n\n" + json.dumps(THREE[1]) + "\n"

        assert parse_documents(text, format="jsonl") == THREE[:2]

    def test_toml_says_it_with_a_list_key(self):
        """TOML's [[searches]] is the cross-format spelling of "---"."""
        text = '[[searches]]\ntitle = "One"\n\n[[searches]]\ntitle = "Two"\n'

        assert parse_documents(text, format="toml") == [
            {"searches": [{"title": "One"}, {"title": "Two"}]}
        ]


class TestFormatDetection:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("spec.yaml", SpecFormat.YAML),
            ("spec.yml", SpecFormat.YAML),
            ("spec.YAML", SpecFormat.YAML),
            ("spec.json", SpecFormat.JSON),
            ("spec.jsonl", SpecFormat.JSONL),
            ("spec.ndjson", SpecFormat.JSONL),
            ("spec.toml", SpecFormat.TOML),
        ],
    )
    def test_the_suffix_decides(self, name, expected):
        assert detect_format(name) is expected

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("yaml", SpecFormat.YAML),
            ("yml", SpecFormat.YAML),
            ("JSON", SpecFormat.JSON),
            ("jsonl", SpecFormat.JSONL),
            ("ndjson", SpecFormat.JSONL),
            (".toml", SpecFormat.TOML),
        ],
    )
    def test_an_explicit_format_is_named_however_it_is_spelled(self, name, expected):
        assert detect_format(text="whatever", format=name) is expected

    def test_an_explicit_format_beats_the_suffix(self):
        assert detect_format("spec.yaml", format="json") is SpecFormat.JSON

    @pytest.mark.parametrize("fmt", sorted(ONE_SPEC))
    def test_an_unhelpful_name_falls_back_to_the_content(self, tmp_path, fmt):
        path = write(tmp_path, "spec.txt", ONE_SPEC[fmt])

        assert load_documents(path) == [EXPECTED]

    @pytest.mark.parametrize(
        "text, expected",
        [
            (YAML_TEXT, SpecFormat.YAML),
            (JSON_TEXT, SpecFormat.JSON),
            (TOML_TEXT, SpecFormat.TOML),
            (MULTI["jsonl"], SpecFormat.JSONL),
            ("title: One\n---\ntitle: Two\n", SpecFormat.YAML),
        ],
    )
    def test_sniffing_names_the_format(self, text, expected):
        """A one-line JSONL file is a JSON file, and parses the same either
        way, which is why the JSONL case here has three lines."""
        assert detect_format(text=text) is expected

    def test_an_unknown_format_names_the_known_ones(self):
        with pytest.raises(PhabfiveInputException) as error:
            detect_format(text="x", format="xml")

        message = str(error.value)
        assert "xml" in message
        for known in ("yaml", "json", "jsonl", "toml"):
            assert known in message

    def test_nothing_to_go_on_is_refused(self):
        with pytest.raises(PhabfiveInputException):
            detect_format("spec.txt")


class TestParseErrorsNameFileFormatAndPosition:
    BROKEN = {
        "yaml": "search:\n  priority: high\n   column: nope\n",
        "json": '{"search": {"priority": "high",}}\n',
        "jsonl": '{"title": "One"}\n{"title": nope}\n',
        "toml": 'title = "One"\ntitle = \n',
    }

    @pytest.mark.parametrize("fmt", sorted(BROKEN))
    def test_a_broken_file_names_the_file_the_format_and_a_line(self, tmp_path, fmt):
        path = write(tmp_path, f"broken{SUFFIXES[fmt]}", self.BROKEN[fmt])

        with pytest.raises(PhabfiveDataException) as error:
            load_documents(path)

        message = str(error.value)
        assert str(path) in message
        assert fmt in message
        assert "line" in message

    @pytest.mark.parametrize("fmt", sorted(BROKEN))
    def test_broken_text_names_the_source_it_was_given(self, fmt):
        with pytest.raises(PhabfiveDataException) as error:
            parse_documents(self.BROKEN[fmt], format=fmt, source="request body")

        assert "request body" in str(error.value)

    @pytest.mark.parametrize("fmt", sorted(BROKEN))
    def test_text_with_no_source_is_named_anyway(self, fmt):
        with pytest.raises(PhabfiveDataException) as error:
            parse_documents(self.BROKEN[fmt], format=fmt)

        assert "<data>" in str(error.value)


class TestStructureRaises:
    """No coherent document means no spec to hang a problem on."""

    @pytest.mark.parametrize(
        "fmt, text",
        [
            ("yaml", "- one\n- two\n"),
            ("json", '"just a string"'),
            ("jsonl", "[1, 2, 3]\n"),
        ],
    )
    def test_a_root_that_is_not_a_mapping_is_refused(self, fmt, text):
        with pytest.raises(PhabfiveDataException) as error:
            parse_documents(text, format=fmt)

        assert "mapping" in str(error.value)

    def test_the_offending_document_is_numbered(self):
        text = "title: One\n---\n- not a mapping\n"

        with pytest.raises(PhabfiveDataException) as error:
            parse_documents(text, format="yaml")

        assert "2" in str(error.value)

    @pytest.mark.parametrize("fmt", ["yaml", "json", "jsonl", "toml"])
    def test_an_empty_spec_is_refused(self, fmt):
        with pytest.raises(PhabfiveDataException) as error:
            parse_documents("", format=fmt)

        assert "empty" in str(error.value)

    def test_a_missing_file_is_a_config_problem(self, tmp_path):
        with pytest.raises(PhabfiveConfigException) as error:
            load_documents(tmp_path / "absent.yaml")

        assert "absent.yaml" in str(error.value)

    def test_a_directory_is_not_a_spec(self, tmp_path):
        with pytest.raises(PhabfiveConfigException) as error:
            load_documents(tmp_path)

        assert "not a file" in str(error.value)

    def test_bytes_that_are_not_text(self, tmp_path):
        path = tmp_path / "spec.yaml"
        path.write_bytes(b"\xff\xfe\x00binary")

        with pytest.raises(PhabfiveDataException) as error:
            load_documents(path)

        assert "UTF-8" in str(error.value)


class TestSpecEntryPoints:
    """load_spec and parse_spec are a pair, and neither reads the other's world.

    Phase 1 of #463 has them answer with the parsed documents; #469 wraps
    that in a Spec. What is asserted here is what survives that change: the
    pairing, and the errors.
    """

    def test_load_spec_and_parse_spec_agree(self, tmp_path):
        path = write(tmp_path, "spec.yaml", YAML_TEXT)

        assert load_spec(path) == parse_spec(YAML_TEXT, format="yaml")

    @pytest.mark.parametrize("kind", ["create", "search"])
    def test_a_supplied_kind_is_accepted(self, kind):
        assert parse_spec(YAML_TEXT, format="yaml", kind=kind)

    def test_an_unknown_kind_names_both(self):
        with pytest.raises(PhabfiveInputException) as error:
            parse_spec(YAML_TEXT, format="yaml", kind="destroy")

        message = str(error.value)
        assert "create" in message
        assert "search" in message

    def test_the_kind_is_checked_before_the_text_is_parsed(self):
        """A bad argument is the caller's mistake, and outranks bad data."""
        with pytest.raises(PhabfiveInputException):
            parse_spec("not: [valid", format="yaml", kind="destroy")

    def test_load_spec_reports_a_missing_file(self, tmp_path):
        with pytest.raises(PhabfiveConfigException):
            load_spec(tmp_path / "absent.yaml")


class TestLibraryHygiene:
    """phabfive/spec/ is library code; see CLAUDE.md."""

    def test_loading_a_spec_does_not_touch_the_environment(self, tmp_path):
        import os

        path = write(tmp_path, "spec.yaml", YAML_TEXT)
        before = dict(os.environ)

        load_spec(path)

        assert dict(os.environ) == before
