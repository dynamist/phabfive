# python std lib
import os

# phabfive imports
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException
from phabfive.user import User

# 3rd party imports
import mock
import pytest
from mock import patch, Mock


def test_user_class():
    """
    Basic class tests for issues caused when creating the object and that it inherits from 
    the correct parent class that we expect
    """
    u = User()

    # Validate that User class inherits from Phabfive parent class
    assert Phabfive in u.__class__.__bases__


@mock.patch.dict(os.environ, {"PHAB_URL": "", "PHAB_TOKEN": ""})
def test_empty_url_or_token():
    """
    Validate that Diffusion works the same way as Phabricator parent class
    """
    with pytest.raises(PhabfiveConfigException) as ex:
        u = User()


def test_whoami():
    pass
