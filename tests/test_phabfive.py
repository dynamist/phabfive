# -*- coding: utf-8 -*-

# python std lib
from unittest import mock

# 3rd party imports
import pytest


def test_import_version():
    try:
        from phabfive import __version__  # noqa
    except ImportError:
        pytest.fail("Unexpected ImportError")


def test_import_phabfive():
    try:
        from phabfive.core import Phabfive  # noqa
    except ImportError:
        pytest.fail("Unexpected ImportError")


class TestAutoFormatDetection:
    """Tests for automatic format detection based on TTY status."""

    def test_get_auto_format_terminal(self):
        """Test auto-format returns 'rich' when stdout is a TTY."""
        from phabfive.core import Phabfive

        with mock.patch('sys.stdout.isatty', return_value=True):
            result = Phabfive._get_auto_format()
            assert result == "rich"

    def test_get_auto_format_piped(self):
        """Test auto-format returns 'strict' when stdout is piped/redirected."""
        from phabfive.core import Phabfive

        with mock.patch('sys.stdout.isatty', return_value=False):
            result = Phabfive._get_auto_format()
            assert result == "strict"
