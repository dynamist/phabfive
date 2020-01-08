# python std lib
import os

# phabfive imports
from phabfive.core import Phabfive
from phabfive.diffusion import Diffusion
from phabfive.exceptions import PhabfiveConfigException

# 3rd party imports
import mock
import pytest
from mock import patch, Mock


@mock.patch.dict(
    os.environ,
    {
        "PHAB_URL": "http://127.0.0.1/api/",
        "PHAB_TOKEN": "api-bjmudcq4mjtxprkk7w3s4fkdqz6z",
    },
)
def test_diffusion_class():
    """
    Basic class tests for issues caused when creating the object and that it inherits from 
    the correct parent class that we expect
    """
    d = Diffusion()

    # Validate that Diffusion class inherits from Phabfive parent class
    assert Phabfive in d.__class__.__bases__


@mock.patch.dict(os.environ, {"PHAB_URL": "", "PHAB_TOKEN": ""})
def test_empty_url_or_token():
    """
    Validate that Diffusion works the same way as Phabricator parent class
    """
    with pytest.raises(PhabfiveConfigException) as ex:
        d = Diffusion()


@mock.patch.dict(
    os.environ,
    {
        "PHAB_URL": "http://127.0.0.1/api/",
        "PHAB_TOKEN": "api-bjmudcq4mjtxprkk7w3s4fkdqz6z",
    },
)
def test_validator():
    """
    We assume that passphrase identifier validator is
        R[0-9]+
    """
    d = Diffusion()
    
    assert d._validate_identifier('R1') is not None
    assert d._validate_identifier('foobar') is None
