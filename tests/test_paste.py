# python std lib
import os

# phabfive imports
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.paste import Paste

# 3rd party imports
import mock
import pytest
from mock import patch, Mock


def test_paste_class():
    """
    Basic class tests for issues caused when creating the object and that it inherits from 
    the correct parent class that we expect
    """
    p = Paste()

    # Validate that Paste class inherits from Phabfive parent class
    assert Phabfive in p.__class__.__bases__


@mock.patch.dict(os.environ, {"PHAB_URL": "", "PHAB_TOKEN": ""})
def test_empty_url_or_token():
    """
    Validate that Diffusion works the same way as Phabricator parent class
    """
    with pytest.raises(PhabfiveConfigException) as ex:
        p = Paste()


def test_validator():
    """
    We assume that passphrase identifier validator is
        P[0-9]+
    """
    p = Paste()
    
    assert p._validate_identifier('P1') is not None
    assert p._validate_identifier('foobar') is None


def test_convert_ids():
    """
    """
    p = Paste()

    # No ID:s to convert so return empty list
    with pytest.raises(PhabfiveDataException):
        p._convert_ids(None)

    assert p._convert_ids([]) == []

    # If we pass in a ID that will not validate against the MONOGRAMS
    with pytest.raises(PhabfiveDataException):
        p._convert_ids(['foobar'])

    assert p._convert_ids(['P1', 'P11']) == [1, 11]
