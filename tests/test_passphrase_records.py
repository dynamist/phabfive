# -*- coding: utf-8 -*-
"""The shape of a credential's machine-readable record, and which secrets it carries."""

import json

from ruamel.yaml import YAML

from phabfive.passphrase.display import (
    build_passphrase_record,
    display_passphrase_json,
    display_passphrase_yaml,
    display_passphrases_json,
    display_passphrases_yaml,
)


def _credential(**extra):
    return {
        "id": "K1",
        "url": "https://phorge.example.com/K1",
        "type": "Note",
        "name": "Deployment notes",
        "dateCreated": 1234567800,
        "dateModified": 1234567850,
        **extra,
    }


class TestRecordShape:
    def test_link_then_a_credential_section(self):
        record = build_passphrase_record(_credential(secret="s"))

        assert list(record) == ["Link", "Credential"]
        assert record["Link"] == "https://phorge.example.com/K1"

    def test_name_comes_first(self):
        record = build_passphrase_record(_credential(username="deploy", secret="s"))

        assert list(record["Credential"]) == [
            "Name",
            "Type",
            "Username",
            "Secret",
            "Created",
            "Modified",
        ]

    def test_every_format_emits_the_same_record(self, capsys):
        cred = _credential(secret="line one\nline two", public_key="ssh-rsa AAAA")

        display_passphrases_json([cred], output_format="json")
        as_json = json.loads(capsys.readouterr().out)

        display_passphrases_json([cred], output_format="jsonl")
        as_jsonl = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

        display_passphrases_yaml([cred])
        as_yaml = YAML(typ="safe").load(capsys.readouterr().out)

        assert as_json == as_jsonl == as_yaml == [build_passphrase_record(cred)]

    def test_multiline_secret_is_a_yaml_block_scalar(self, capsys):
        display_passphrase_yaml(_credential(secret="line one\nline two"))

        assert "    Secret: |-\n" in capsys.readouterr().out


class TestSecretPolicy:
    def test_search_hides_the_secret_unless_asked(self):
        cred = _credential(secret="s")

        assert (
            "Secret"
            not in build_passphrase_record(cred, show_secrets=False)["Credential"]
        )
        assert build_passphrase_record(cred)["Credential"]["Secret"] == "s"

    def test_no_secret_key_when_none_was_fetched(self):
        assert "Secret" not in build_passphrase_record(_credential())["Credential"]

    def test_single_show_always_carries_a_secret_key(self, capsys):
        display_passphrase_json(_credential())

        assert json.loads(capsys.readouterr().out)["Credential"]["Secret"] == ""
