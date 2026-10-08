# -*- coding: utf-8 -*-
"""The shape of a credential's machine-readable record, and which secrets it carries."""

import json
from unittest.mock import MagicMock, patch

import pytest
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

    def test_a_hidden_secret_is_no_key_rather_than_an_empty_one(self, capsys):
        """An empty Secret would read as a credential whose secret is empty."""
        display_passphrase_json(_credential(), show_secrets=False)

        assert "Secret" not in json.loads(capsys.readouterr().out)["Credential"]


class TestShowCommand:
    """`passphrase show` fetches and prints the secret only when asked."""

    def _invoke(self, args):
        from typer.testing import CliRunner

        from phabfive.cli import app

        mock_app = MagicMock()
        mock_app.get_passphrases.return_value = [
            _credential(secret="hunter2", public_key="ssh-rsa AAAA")
        ]
        with patch(
            "phabfive.cli.passphrase._get_passphrase_app", return_value=mock_app
        ):
            result = CliRunner().invoke(app, args)
        return result, mock_app

    def test_the_secret_is_not_even_fetched_by_default(self):
        result, mock_app = self._invoke(["--format=json", "passphrase", "show", "K1"])

        assert result.exit_code == 0
        assert mock_app.get_passphrases.call_args.kwargs == {
            "need_secrets": False,
            "need_public_keys": True,
        }
        assert "Secret" not in json.loads(result.stdout)["Credential"]

    @pytest.mark.parametrize("output_format", ["rich", "tree"])
    def test_rich_and_tree_say_the_secret_is_hidden(self, output_format):
        from io import StringIO

        from rich.console import Console

        from phabfive.passphrase.display import display_passphrase

        out = StringIO()
        instance = MagicMock()
        instance.get_console.return_value = Console(file=out, width=200)

        display_passphrase(
            _credential(_link="K1"), output_format, instance, show_secrets=False
        )

        assert "Secret: hidden (use --show-secret)" in out.getvalue()

    @pytest.mark.parametrize("flag", ["--show-secret", "-s"])
    def test_show_secret_reveals_it(self, flag):
        result, mock_app = self._invoke(
            ["--format=json", "passphrase", "show", "K1", flag]
        )

        assert mock_app.get_passphrases.call_args.kwargs["need_secrets"] is True
        assert json.loads(result.stdout)["Credential"]["Secret"] == "hunter2"

    def test_value_without_show_secret_is_refused_before_connecting(self):
        """Refused before the app is built, which is what connects and verifies."""
        from typer.testing import CliRunner

        from phabfive.cli import app

        with patch("phabfive.cli.passphrase._get_passphrase_app") as get_app:
            result = CliRunner().invoke(
                app, ["--format=value", "passphrase", "show", "K1"]
            )

        assert result.exit_code == 1
        assert "--show-secret" in result.output
        get_app.assert_not_called()

    @pytest.mark.parametrize("flag", ["-n", "-P", "--no-secret"])
    def test_the_old_flags_are_gone(self, flag):
        result, _ = self._invoke(["passphrase", "show", "K1", flag])

        assert result.exit_code == 2
