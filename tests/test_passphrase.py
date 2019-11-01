# python std lib
import os

# phabfive imports
from phabfive.core import Phabfive
from phabfive.passphrase import Passphrase
from phabfive.exceptions import PhabfiveConfigException

# 3rd party imports
import mock
import pytest
from mock import patch, Mock


def test_passphrase_class():
    """
    Basic class tests for issues caused when creating the object and that it inherits from 
    the correct parent class that we expect.
    """
    p = Passphrase()

    # Validate that Passphrase class inherits from Phabfive parent class
    assert Phabfive in p.__class__.__bases__


@mock.patch.dict(os.environ, {"PHAB_URL": "", "PHAB_TOKEN": ""})
def test_empty_url_or_token():
    """
    Validate that Diffusion works the same way as Phabricator parent class
    """
    with pytest.raises(PhabfiveConfigException) as ex:
        p = Passphrase()


def test_validator():
    """
    We assume that passphrase identifier validator is
        K[0-9]+
    """
    p = Passphrase()
    
    assert p._validate_identifier('K1') is not None
    assert p._validate_identifier('foobar') is None
