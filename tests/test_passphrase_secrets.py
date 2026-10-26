# -*- coding: utf-8 -*-
"""What `passphrase show` says about a credential, and a secret Phorge does not return.

passphrase.query sends a secret only for the types it has a material key for,
only when the credential allows Conduit access and is not locked, and only
while the secret has not been destroyed. Anything else must come back as no
secret and a reason - never as an empty or placeholder string that a
`$(phabfive K1 -s)` would take for the value.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app
from phabfive.exceptions import PhabfiveDataException
from phabfive.passphrase import Passphrase
from phabfive.passphrase.core import extract_secret
from phabfive.passphrase.display import build_passphrase_record

URL = "https://phorge.example.com"

NO_API_ACCESS = {
    "noAPIAccess": "This private material for this credential is not accessible "
    "via API calls."
}


def _query_record(id_, type_, material, **extra):
    """One credential as passphrase.query returns it."""
    return {
        "id": str(id_),
        "phid": f"PHID-CDTL-{id_}",
        "type": type_,
        "name": f"Credential {id_}",
        "description": "",
        "uri": f"{URL}/K{id_}",
        "monogram": f"K{id_}",
        "username": "",
        "material": material,
        **extra,
    }


CREDENTIALS = [
    _query_record(
        1,
        "note",
        [],
        name="Deployment notes",
        description="What the note is for",
    ),
    _query_record(
        2,
        "ssh-generated-key",
        {**NO_API_ACCESS, "publicKey": "ssh-rsa AAAA"},
        username="git",
        description="url: ssh://git@example.com\nhost key: pinned",
    ),
    _query_record(4, "password", {"password": "hunter2"}, username="buildbot"),
    _query_record(5, "token", {"token": "not-a-real-token"}),
]


@pytest.fixture
def passphrase():
    with patch("phabfive.passphrase.core.Phabfive.__init__", return_value=None):
        passphrase = Passphrase()
    passphrase.phab = MagicMock()
    passphrase.url = URL

    def query(ids=None, needSecrets=0, **_):
        records = [c for c in CREDENTIALS if ids is None or c["id"] in ids]
        if not needSecrets:
            # Without secrets Conduit still says noAPIAccess, and nothing else
            records = [
                {
                    **c,
                    "material": NO_API_ACCESS if "noAPIAccess" in c["material"] else [],
                }
                for c in records
            ]
        return {"data": {c["phid"]: c for c in records}}

    passphrase.phab.passphrase.query.side_effect = query
    passphrase._get_credential_dates = MagicMock(return_value=(None, None))
    return passphrase


class TestExtractSecret:
    @pytest.mark.parametrize(
        "type_, material",
        [
            ("password", {"password": "s3cret"}),
            ("token", {"token": "s3cret"}),
            ("ssh-generated-key", {"privateKey": "s3cret", "publicKey": "ssh-rsa"}),
            ("ssh-key-text", {"privateKey": "s3cret"}),
            ("ssh-key-file", {"file": "s3cret"}),
        ],
    )
    def test_each_type_reads_its_own_key(self, type_, material):
        assert extract_secret(_query_record(9, type_, material), f"{URL}/K9") == (
            "s3cret",
            None,
        )

    def test_an_empty_secret_is_still_a_secret(self):
        """Phorge sent the key: the secret really is empty."""
        record = _query_record(9, "password", {"password": ""})

        assert extract_secret(record, f"{URL}/K9") == ("", None)

    def test_no_conduit_access_names_the_url(self):
        secret, reason = extract_secret(
            _query_record(9, "token", NO_API_ACCESS), f"{URL}/K9"
        )

        assert secret is None
        assert "K9 does not allow Conduit access" in reason
        assert f"{URL}/K9" in reason

    @pytest.mark.parametrize("material", [[], {}])
    def test_a_note_never_has_one(self, material):
        """passphrase.query has no material key for a note, so its text never comes."""
        secret, reason = extract_secret(_query_record(9, "note", material), "u")

        assert secret is None
        assert "does not return a note's text over Conduit" in reason

    def test_a_destroyed_secret(self):
        record = _query_record(9, "token", {"destroyed": "destroyed"})

        secret, reason = extract_secret(record, "u")

        assert secret is None
        assert "destroyed" in reason

    def test_an_unknown_type(self):
        secret, reason = extract_secret(_query_record(9, "smartcard", {}), "u")

        assert secret is None
        assert "does not return this secret over Conduit" in reason

    def test_a_known_type_without_its_key(self):
        secret, reason = extract_secret(_query_record(9, "token", {}), "u")

        assert secret is None
        assert "returned no secret for K9" in reason


class TestLibrary:
    def test_get_passphrases_keeps_the_others(self, passphrase):
        creds = passphrase.get_passphrases(["K1", "K2", "K4"], need_secrets=True)

        assert [c["secret"] for c in creds] == [None, None, "hunter2"]
        assert "note's text" in creds[0]["secret_error"]
        assert "Conduit access" in creds[1]["secret_error"]
        assert "secret_error" not in creds[2]

    def test_no_secret_no_error(self, passphrase):
        """Without secrets asked for, nothing is missing."""
        creds = passphrase.get_passphrases(["K1", "K2"], need_secrets=False)

        assert all("secret" not in c and "secret_error" not in c for c in creds)

    def test_conduit_access_is_known_without_the_secret(self, passphrase):
        creds = passphrase.get_passphrases(["K2", "K4"], need_secrets=False)

        assert [c["conduit_access"] for c in creds] == [False, True]

    def test_description_and_username(self, passphrase):
        cred = passphrase.get_passphrases(["K2"], need_secrets=False)[0]

        assert cred["description"] == "url: ssh://git@example.com\nhost key: pinned"
        assert cred["username"] == "git"

    @pytest.mark.parametrize("id_", ["K1", "K2"])
    def test_get_secret_raises_rather_than_return_a_placeholder(self, passphrase, id_):
        with pytest.raises(PhabfiveDataException, match=f"{URL}/{id_}"):
            passphrase.get_secret(id_)

    def test_get_secret(self, passphrase):
        assert passphrase.get_secret("K5") == "not-a-real-token"


class TestRecord:
    def test_description_is_always_there(self):
        record = build_passphrase_record({"name": "n", "type": "Token"})

        assert record["Credential"]["Description"] == ""

    def test_username_only_when_non_empty(self):
        with_one = build_passphrase_record({"username": "git"})["Credential"]
        without = build_passphrase_record({"username": ""})["Credential"]

        assert with_one["Username"] == "git"
        assert "Username" not in without

    def test_an_unreadable_secret_is_null(self):
        record = build_passphrase_record({"secret": None}, always_secret=True)

        assert record["Credential"]["Secret"] is None


def _invoke(passphrase, *args):
    with patch("phabfive.cli.passphrase._get_passphrase_app", return_value=passphrase):
        return CliRunner().invoke(app, list(args))


class TestShow:
    @pytest.mark.parametrize("output_format", ["json", "jsonl", "yaml"])
    def test_description_and_username_without_the_secret(
        self, passphrase, output_format
    ):
        """#592: what a credential is for, without having to read its secret."""
        from ruamel.yaml import YAML

        result = _invoke(
            passphrase, f"--format={output_format}", "passphrase", "show", "K2"
        )

        assert result.exit_code == 0, result.output
        record = YAML(typ="safe").load(result.stdout)
        if isinstance(record, list):
            record = record[0]
        assert record["Credential"]["Description"] == (
            "url: ssh://git@example.com\nhost key: pinned"
        )
        assert record["Credential"]["Username"] == "git"
        assert record["Credential"]["ConduitAccess"] is False
        assert "Secret" not in record["Credential"]

    def test_multiline_description_is_a_yaml_block_scalar(self, passphrase):
        result = _invoke(passphrase, "--format=yaml", "passphrase", "show", "K2")

        assert "Description: |-\n" in result.stdout

    @pytest.mark.parametrize("output_format", ["rich", "tree"])
    def test_rich_and_tree_show_description_and_conduit_access(
        self, passphrase, output_format
    ):
        result = _invoke(
            passphrase, f"--format={output_format}", "passphrase", "show", "K2"
        )

        assert result.exit_code == 0, result.output
        assert "Username: git" in result.stdout
        assert "url: ssh://git@example.com" in result.stdout
        assert "host key: pinned" in result.stdout
        assert "ConduitAccess: disabled" in result.stdout

    @pytest.mark.parametrize("id_", ["K1", "K2"])
    def test_an_unreadable_secret_fails(self, passphrase, id_):
        """#593: no placeholder in the secret's place, the reason on stderr, exit 1."""
        result = _invoke(passphrase, "--format=json", "passphrase", "show", id_, "-s")

        assert result.exit_code == 1
        assert json.loads(result.stdout)["Credential"]["Secret"] is None
        assert f"{URL}/{id_}" in result.stderr
        assert "Access denied" not in result.stdout

    def test_value_prints_nothing_for_an_unreadable_secret(self, passphrase):
        result = _invoke(passphrase, "--format=value", "passphrase", "show", "K2", "-s")

        assert result.exit_code == 1
        assert result.stdout == ""
        assert "K2 does not allow Conduit access" in result.stderr

    @pytest.mark.parametrize("output_format", ["rich", "tree"])
    def test_rich_and_tree_say_it_is_unavailable(self, passphrase, output_format):
        result = _invoke(
            passphrase, f"--format={output_format}", "passphrase", "show", "K1", "-s"
        )

        assert result.exit_code == 1
        assert "Secret: unavailable (see the error)" in result.stdout

    def test_several_ids_print_the_successes_and_still_fail(self, passphrase):
        result = _invoke(
            passphrase, "--format=json", "passphrase", "show", "K1", "K2", "K4", "-s"
        )

        assert result.exit_code == 1
        secrets = [r["Credential"]["Secret"] for r in json.loads(result.stdout)]
        assert secrets == [None, None, "hunter2"]
        assert result.stderr.count("ERROR:") == 2

    def test_several_ids_value_prints_only_the_readable(self, passphrase):
        result = _invoke(
            passphrase, "--format=value", "passphrase", "show", "K2,K4,K5", "-s"
        )

        assert result.exit_code == 1
        assert result.stdout.splitlines() == ["hunter2", "not-a-real-token"]

    def test_without_show_secret_nothing_fails(self, passphrase):
        result = _invoke(passphrase, "--format=json", "passphrase", "show", "K1", "K2")

        assert result.exit_code == 0
        assert result.stderr == ""


class TestSearch:
    def test_an_unreadable_secret_is_a_warning(self, passphrase):
        """A search lists what exists, so it warns rather than fails."""
        result = _invoke(
            passphrase, "--format=json", "passphrase", "search", "Credential", "-s"
        )

        assert result.exit_code == 0, result.output
        assert "WARNING: K2 does not allow Conduit access" in result.stderr
        records = {r["Link"]: r["Credential"] for r in json.loads(result.stdout)}
        assert records[f"{URL}/K2"]["Secret"] is None
        assert records[f"{URL}/K4"]["Secret"] == "hunter2"
