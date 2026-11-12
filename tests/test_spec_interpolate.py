# -*- coding: utf-8 -*-

"""`interpolate: false`: a document whose strings are text, not templates.

Every string in a spec is a Jinja2 template, and a name it mentions that
nothing declares is an error. That is the right rule for a spec somebody
wrote and the wrong one for text that only *quotes* a template - a task
description showing a Helm value, say - which has no spelling that survives
both the check and the render. The envelope key is the way out, and it is
what a document written from an existing object carries.
"""

import pytest

from phabfive.exceptions import PhabfiveDataException
from phabfive.spec import Spec, build_schema
from phabfive.spec.envelope import ENVELOPE_KEYS

QUOTED = "Set image.tag to {{ .Values.tag }} and {{ release }}"


def create_spec(**envelope):
    return Spec.from_data(
        {
            "kind": "create",
            **envelope,
            "tasks": [{"title": "Document {{ release }}", "description": QUOTED}],
        }
    )


def codes(problems):
    return [problem.code for problem in problems]


class TestWithoutIt:
    """What the key exists to get around, pinned so it stays true."""

    def test_a_quoted_template_is_an_undefined_variable(self):
        problems = create_spec().validate_offline()

        assert set(codes(problems)) == {"undefined-variable"}

    def test_the_raw_block_escape_passes_but_is_never_rendered_away(self):
        """Why `{% raw %}` is not the answer.

        It names no variable, so it validates - and a spec with no
        variables is not rendered by the command, so the escape itself
        would be what reaches the server.
        """
        spec = Spec.from_data(
            {
                "kind": "create",
                "tasks": [{"title": "{% raw %}{{ release }}{% endraw %}"}],
            }
        )

        assert spec.validate_offline() == []
        assert spec.items("task")[0]["title"].startswith("{% raw %}")


class TestWithIt:
    def test_it_is_an_envelope_key_and_not_part_of_the_body(self):
        spec = create_spec(interpolate=False)

        assert "interpolate" in ENVELOPE_KEYS
        assert "interpolate" not in spec.body
        assert spec.envelope.interpolate is False
        assert spec.interpolates is False

    def test_a_document_that_says_nothing_interpolates(self):
        spec = create_spec()

        assert spec.envelope.interpolate is True
        assert spec.interpolates is True

    def test_a_quoted_template_is_clean(self):
        assert create_spec(interpolate=False).validate_offline() == []

    def test_rendering_leaves_every_string_as_written(self):
        rendered = create_spec(interpolate=False).render()

        assert rendered.rendered is True
        assert rendered.items("task")[0] == {
            "title": "Document {{ release }}",
            "description": QUOTED,
        }

    def test_the_other_checks_still_run(self):
        spec = Spec.from_data(
            {
                "kind": "create",
                "interpolate": False,
                "tasks": [{"title": "{{ release }}", "colour": "red"}],
            }
        )

        assert codes(spec.validate_offline()) == ["unknown-key"]

    def test_a_search_spec_takes_it_too(self):
        spec = Spec.from_data(
            {
                "kind": "search",
                "interpolate": False,
                "searches": [{"search": {"text_query": "{{ release }}"}}],
            }
        )

        assert spec.validate_offline() == []

    def test_explicit_true_is_the_default_spelled_out(self):
        assert set(codes(create_spec(interpolate=True).validate_offline())) == {
            "undefined-variable"
        }


class TestVariablesHaveNowhereToGo:
    def test_a_declared_variable_is_refused(self):
        spec = Spec.from_data(
            {
                "kind": "create",
                "interpolate": False,
                "variables": {"release": "1.0"},
                "tasks": [{"title": "Document {{ release }}"}],
            }
        )

        problems = spec.validate_offline()

        assert codes(problems) == ["interpolation-off"]
        assert problems[0].field == "release"

    def test_a_supplied_variable_is_refused(self):
        problems = create_spec(interpolate=False).validate_offline(
            variables={"release": "1.0"}
        )

        assert codes(problems) == ["interpolation-off"]

    def test_one_name_declared_and_supplied_is_reported_once(self):
        spec = Spec.from_data(
            {
                "kind": "create",
                "interpolate": False,
                "variables": {"release": "1.0"},
                "tasks": [{"title": "t"}],
            }
        )

        assert codes(spec.validate_offline(variables={"release": "2.0"})) == [
            "interpolation-off"
        ]

    def test_rendering_with_values_raises_rather_than_dropping_them(self):
        with pytest.raises(PhabfiveDataException, match="interpolate: false"):
            create_spec(interpolate=False).render({"release": "1.0"})


class TestTheValueIsABoolean:
    @pytest.mark.parametrize("value", ["false", "no", 0, None])
    def test_anything_else_is_reported_and_does_not_switch_it_off(self, value):
        spec = create_spec(interpolate=value)

        assert spec.interpolates is True
        assert "wrong-type" in codes(spec.validate_offline())


class TestItSurvivesTheRoundTrip:
    def test_to_data_writes_it_back(self):
        spec = create_spec(interpolate=False)
        data = spec.to_data()

        assert data["interpolate"] is False
        assert Spec.from_data(data) == spec

    def test_to_data_does_not_grow_one(self):
        assert "interpolate" not in create_spec().to_data()

    def test_documents_that_disagree_are_refused(self):
        with pytest.raises(PhabfiveDataException, match="disagree about interpolate"):
            Spec.from_data(
                [
                    {"kind": "create", "interpolate": False, "tasks": [{"title": "a"}]},
                    {"interpolate": True, "tasks": [{"title": "b"}]},
                ]
            )

    def test_one_document_saying_it_covers_the_file(self):
        spec = Spec.from_data(
            [
                {"kind": "create", "interpolate": False, "tasks": [{"title": "a"}]},
                {"tasks": [{"title": "{{ b }}"}]},
            ]
        )

        assert spec.validate_offline() == []


class TestTheSchemaKnowsIt:
    @pytest.mark.parametrize("kind", ["create", "search"])
    def test_it_is_a_boolean_property(self, kind):
        assert build_schema(kind)["properties"]["interpolate"]["type"] == "boolean"
