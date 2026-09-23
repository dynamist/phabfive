# -*- coding: utf-8 -*-

"""Passphrase credentials cannot be created, and phabfive says why (#484).

Phorge exposes `passphrase.query` and nothing else. There is no
`passphrase.edit` and no `passphrase.create`, which is why
`phabfive/cli/passphrase.py` carries only `show` and `search`. So a
`passphrases:` section in a create spec is not a feature phabfive has yet to
write - it is a method that does not exist to call.

That makes it an **offline** refusal: no token is needed to know that an
endpoint is missing, and a spec repository has to be checkable in CI on a
machine that has never seen one.

Two promises are pinned here, and the second is the one that rots:

1. The section is refused, with the code `not-creatable` and a sentence
   naming the missing endpoint - not `unknown-key`, because `passphrases:`
   is spelled correctly and a "Did you mean 'pastes'?" would be a lie.
2. The refusal offers no way out. A message hinting at a flag, a token, a
   permission or a newer server would send a reader looking for something
   that cannot be found, and every one of those words is asserted absent.

Reading credentials is untouched: a passphrase *search* spec is ordinary and
validates clean, which is asserted next to the refusal so the two cannot
drift into "passphrases are unsupported".

`phabfive edit K123` has the same cause and now says the same thing, so its
sentence is checked here too rather than in a second file.
"""

# python std lib
from unittest import mock

# 3rd party imports
import pytest

# phabfive imports
from phabfive.spec import Kind, Severity, parse_spec, validate_offline
from phabfive.spec.problems import CODES, Layer
from phabfive.spec.references import CREATE_OBJECT_KEYS, UNCREATABLE_OBJECT_KEYS

#: Words that would each promise the refusal is conditional on something the
#: reader could go and change. None of them can change whether a Conduit
#: method exists.
NO_WAY_OUT = (
    "flag",
    "option",
    "permission",
    "policy",
    "token",
    "version",
    "upgrade",
    "enable",
    "not yet",
    "coming",
    "planned",
    "unsupported",
    "unimplemented",
    "not implemented",
)

A_CREDENTIAL = """\
kind: create
passphrases:
  - name: Deploy key
    type: ssh-generated-key
"""

MIXED = """\
tasks:
  - title: Bootstrap the environment
passphrases:
  - name: Deploy key
"""

A_PASSPHRASE_SEARCH = """\
kind: search
searches:
  - type: passphrase
    title: Deploy credentials
    search:
      type: key
      limit: 10
"""


def check(text, *, kind=None):
    """Validate a YAML spec written inline, offline and with no token."""
    return validate_offline(parse_spec(text, format="yaml", kind=kind))


def refusals(problems):
    """Only the `not-creatable` records of a report."""
    return [one for one in problems if one.code == "not-creatable"]


class TestACreateSpecRefusesPassphrases:
    """The section is refused, offline, with the reason."""

    def test_a_passphrases_section_is_refused(self):
        problems = check(A_CREDENTIAL)

        assert [one.code for one in problems] == ["not-creatable"]

    def test_the_refusal_is_offline_and_an_error(self):
        """No token, and it fails a build rather than warning."""
        one = refusals(check(A_CREDENTIAL))[0]

        assert one.layer == Layer.OFFLINE
        assert one.severity == Severity.ERROR

    def test_the_refusal_names_the_missing_endpoint(self):
        reason = refusals(check(A_CREDENTIAL))[0].reason

        assert "passphrase.edit" in reason
        assert "web UI" in reason

    def test_the_refusal_says_searching_still_works(self):
        """The sentence has to separate "cannot create" from "unsupported"."""
        assert "search" in refusals(check(A_CREDENTIAL))[0].reason.lower()

    def test_the_refusal_is_the_documented_sentence(self):
        """Verbatim, because #484 wrote it and a paraphrase loses the why."""
        assert refusals(check(A_CREDENTIAL))[0].reason == (
            "passphrases cannot be created: Phorge exposes no passphrase.edit "
            "endpoint. Credentials must be created in the web UI. Passphrase "
            "search specs are supported."
        )

    def test_the_problem_points_at_the_document_and_the_key(self):
        one = refusals(check(A_CREDENTIAL))[0]

        assert one.object == "$"
        assert one.field == "passphrases"

    def test_the_problem_counts_the_section_rather_than_echoing_it(self):
        """A refused `passphrases:` body is credential material.

        `phabfive/cli/spec.py` puts `Problem.as_record()` - `value` and all
        - into `--format=json` and `--format=yaml`, so echoing the section
        would print the secrets of the one create key whose body holds any
        into a stream somebody pipes to `jq`.
        """
        one = refusals(check(A_CREDENTIAL))[0]

        assert one.value == "1 item(s)"
        assert "Deploy key" not in repr(one.as_record())
        assert "ssh-generated-key" not in repr(one.as_record())

    def test_not_creatable_is_a_declared_code(self):
        """The slug a CI job branches on is in the one vocabulary list."""
        assert "not-creatable" in CODES


class TestTheRefusalOffersNoWayOut:
    """Nothing the reader could go and change would change the answer."""

    @pytest.mark.parametrize("word", NO_WAY_OUT)
    def test_the_reason_promises_nothing(self, word):
        assert word not in refusals(check(A_CREDENTIAL))[0].reason.lower()

    def test_no_spelling_correction_is_offered(self):
        """`passphrases` is spelled right; `pastes` is a different thing.

        Reported as `unknown-key` it would come within a hair of a "Did you
        mean 'pastes'?", which is the one suggestion that must never be made
        here - a credential is not a paste.
        """
        reason = refusals(check(A_CREDENTIAL))[0].reason

        assert "Did you mean" not in reason
        assert "pastes" not in reason


class TestItIsReportedOnceAndOnly:
    """One mistake, one problem, and nothing else changes."""

    def test_it_is_not_also_reported_as_an_unknown_key(self):
        codes = [one.code for one in check(A_CREDENTIAL)]

        assert codes.count("not-creatable") == 1
        assert "unknown-key" not in codes

    def test_the_rest_of_the_spec_is_still_validated(self):
        """A refused section does not stop the tasks beside it being read."""
        problems = check(MIXED)

        assert [one.code for one in problems] == ["not-creatable"]
        assert refusals(problems)[0].field == "passphrases"

    def test_a_document_holding_only_passphrases_is_a_create_spec(self):
        """Inference has to reach the sentence that helps.

        Without `passphrases` in the create family this file cannot be told
        apart from a search spec, so the loader raises "does not say what
        kind of spec it is" and the reader never sees the real reason.
        """
        spec = parse_spec("passphrases:\n  - name: Deploy key\n", format="yaml")

        assert spec.kind is Kind.CREATE

    @pytest.mark.parametrize("key", [key for key, _ in UNCREATABLE_OBJECT_KEYS])
    def test_every_refused_key_infers_create(self, key):
        """Drift guard: a key added to the table but not to inference."""
        spec = parse_spec(f"{key}:\n  - name: Whatever\n", format="yaml")

        assert spec.kind is Kind.CREATE
        assert [one.code for one in validate_offline(spec)] == ["not-creatable"]

    def test_a_refused_key_is_not_a_creatable_one(self):
        """The two tables name different sections, or something is creatable
        and refused at once."""
        creatable = {key for key, _ in CREATE_OBJECT_KEYS}
        refused = {key for key, _ in UNCREATABLE_OBJECT_KEYS}

        assert not creatable & refused

    def test_a_search_spec_is_not_told_about_creating(self):
        """`passphrases:` under `kind: search` is an unknown key, as before."""
        problems = check("kind: search\npassphrases: []\n")

        assert [one.code for one in problems] == ["unknown-key"]


class TestSearchingForCredentialsStillWorks:
    """The refusal is about creating, and about nothing else."""

    def test_a_passphrase_search_spec_validates_clean(self):
        assert check(A_PASSPHRASE_SEARCH) == []

    def test_a_search_spec_with_passphrases_beside_tasks_is_clean(self):
        assert (
            check(
                "kind: search\n"
                "searches:\n"
                "  - type: passphrase\n"
                "    search:\n"
                "      type: key\n"
                "  - type: task\n"
                "    search:\n"
                "      status: open\n"
            )
            == []
        )


class TestEditSaysTheSameThing:
    """`phabfive edit K123` has the same cause, so it gives the same reason."""

    def _run(self, object_id):
        from phabfive.cli.edit_flow import run_edit

        edit_app = mock.MagicMock()
        edit_app.parse_object_ids.return_value = [("passphrase", object_id.lstrip("K"))]

        with mock.patch("sys.stdin.isatty", return_value=True):
            with mock.patch("sys.stderr") as stderr:
                status = run_edit(edit_app, object_id=object_id)

        written = "".join(
            call.args[0] for call in stderr.write.call_args_list if call.args
        )
        return status, written

    def test_a_k_monogram_is_refused(self):
        status, _ = self._run("K1")

        assert status == 1

    def test_it_names_the_missing_endpoint(self):
        _, written = self._run("K1")

        assert "passphrase.edit" in written
        assert "web UI" in written

    def test_it_no_longer_says_the_work_is_pending(self):
        """ "Not yet implemented" invited a reader to wait for a release."""
        _, written = self._run("K1")

        assert "not yet implemented" not in written.lower()

    @pytest.mark.parametrize("word", NO_WAY_OUT)
    def test_it_promises_nothing_either(self, word):
        _, written = self._run("K1")

        assert word not in written.lower()
