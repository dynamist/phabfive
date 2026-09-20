# -*- coding: utf-8 -*-
"""Tests for --format=value, the bare-values output format.

``value`` prints values with no keys, no header and no decoration, so that a
secret or a paste body can be piped straight into something else. *Which*
value is each command's own choice - the secret for ``passphrase show``, the
monogram for ``passphrase search``, the content for ``paste show`` - and an
app with no bare value to offer registers no renderer and falls back to rich.

The format used to be spelled ``simple``. That spelling lives on in
FORMAT_ALIASES, rewritten in argv before Typer parses, so every script that
already pipes ``--format=simple`` keeps working.
"""

# python std lib
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app, preprocess_format_alias

runner = CliRunner()


def _credential(**overrides):
    credential = {
        "id": "K1",
        "url": "https://phorge.example.com/K1",
        "type": "Password",
        "name": "deploy",
        "username": "deployer",
        "secret": "hunter2",
    }
    credential.update(overrides)
    return credential


class TestPassphraseValue:
    """A credential's bare value is its secret. That is the whole point."""

    def test_show_prints_the_secret_and_nothing_else(self, capsys):
        from phabfive.passphrase.display import display_passphrase

        display_passphrase(_credential(), "value", MagicMock())

        assert capsys.readouterr().out == "hunter2\n"

    def test_show_prints_one_secret_per_credential(self, capsys):
        from phabfive.passphrase.display import display_passphrases

        display_passphrases(
            [_credential(), _credential(id="K3", secret="correct horse")],
            "value",
            MagicMock(),
        )

        assert capsys.readouterr().out == "hunter2\ncorrect horse\n"

    def test_show_honours_no_secret(self, capsys):
        from phabfive.passphrase.display import display_passphrases

        display_passphrases([_credential()], "value", MagicMock(), show_secrets=False)

        assert capsys.readouterr().out == ""

    def test_a_multiline_secret_is_printed_whole(self, capsys):
        from phabfive.passphrase.display import display_passphrase

        key = "-----BEGIN-----\nkey\n-----END-----"
        display_passphrase(_credential(secret=key), "value", MagicMock())

        assert capsys.readouterr().out == key + "\n"

    def test_search_prints_monograms(self, capsys):
        """search has no secret to print - it answers with the monogram.

        Which value a command picks is the command's choice; ``value`` only
        promises that nothing is printed around it.
        """
        from phabfive.passphrase.display import display_passphrases_list

        display_passphrases_list(
            [_credential(), _credential(id="K3")], "value", MagicMock()
        )

        assert capsys.readouterr().out == "K1\nK3\n"


class TestPasteValue:
    """A paste's bare value is its content."""

    def _paste(self, content):
        return {"pastes": [{"id": "P1", "title": "notes", "content": content}]}

    def test_show_prints_the_content_and_nothing_else(self, capsys):
        from phabfive.cli.paste import _display_pastes

        _display_pastes(self._paste("#!/bin/sh\necho hi"), "value", MagicMock())

        assert capsys.readouterr().out == "#!/bin/sh\necho hi\n"

    def test_show_prints_nothing_without_content(self, capsys):
        from phabfive.cli.paste import _display_pastes

        _display_pastes(self._paste(""), "value", MagicMock())

        assert capsys.readouterr().out == ""


class TestSimpleStillWorks:
    """The old spelling reaches the new format, argv rewrite and all."""

    @pytest.mark.parametrize("argv", [["--format=simple"], ["--format", "simple"]])
    def test_argv_is_rewritten_to_value(self, argv):
        rewritten = preprocess_format_alias(["phabfive", *argv, "K1"])

        assert "simple" not in rewritten
        assert "value" in " ".join(rewritten)

    @patch("phabfive.cli.passphrase._get_passphrase_app")
    def test_the_old_spelling_still_prints_the_secret(self, mock_get_app):
        """End to end, the way a script that predates the rename invokes it.

        CliRunner never reaches cli_entrypoint(), which is where the rewrite
        runs, so the test applies it the same way argv would arrive.
        """
        mock_app = MagicMock()
        mock_app.get_passphrases.return_value = [_credential()]
        mock_get_app.return_value = mock_app

        argv = preprocess_format_alias(
            ["phabfive", "--format=simple", "passphrase", "show", "K1"]
        )
        result = runner.invoke(app, argv[1:])

        assert result.exit_code == 0
        assert result.output == "hunter2\n"
