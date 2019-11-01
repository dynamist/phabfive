import os

from phabricator import Phabricator, APIError
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException, PhabfiveRemoteException

# 3rd party imports
import mock
import pytest
from mock import patch, Mock


def test_class_init():
    """
    Should be possible to create the main core class w/o any errors when nothing is configred
    """
    p = Phabfive()

    # This should always default to False
    assert "PHABFIVE_DEBUG" in p.conf
    assert p.conf["PHABFIVE_DEBUG"] == False


@mock.patch.dict(os.environ, {"PHABFIVE_DEBUG": "1"})
def test_phabfive_debug_envrion():
    """
    """
    p = Phabfive()
    # This should always default to False
    assert "PHABFIVE_DEBUG" in p.conf
    assert p.conf["PHABFIVE_DEBUG"] == "1"


@mock.patch.dict(os.environ, {"PHAB_URL": "", "PHAB_TOKEN": ""})
def test_empty_url_or_token():
    """
    If the config can't figure out a valid url or token it should raise a exception in the code
    """
    with pytest.raises(PhabfiveConfigException) as ex:
        p = Phabfive()


@mock.patch.dict(os.environ, {"PHAB_URL": "foobar", "PHAB_TOKEN": "barfoo"})
def test_validator_phab_url():
    """
    When providing some data to PHAB_URL it should raise a config validation error
    """
    with pytest.raises(PhabfiveConfigException):
        p = Phabfive()


@mock.patch.dict(
    os.environ, {"PHAB_URL": "http://127.0.0.1/api", "PHAB_TOKEN": '1'},
)
def test_validator_phab_token():
    """
    When providing some data to PHAB_URL it should raise a config validation error
    """
    with pytest.raises(PhabfiveConfigException):
        p = Phabfive()


@mock.patch.dict(
    os.environ,
    {
        "PHAB_URL": "http://127.0.0.1/api/",
        "PHAB_TOKEN": "api-bjmudcq4mjtxprkk7w3s4fkdqz6z",
    },
)
def test_whoami_api_error():
    """
    When creating the object, it tries to run user.whoami() method on Phabricator class.

    Validate that when whoami() call returns a internal APIError we should reraise it as a PhabfiveRemoteException exception.
    """
    with patch.object(
        Phabricator, "__getattr__", autospec=True
    ) as dynamic_phabricator_getattr:

        def side_effect(self, *args, **kwargs):
            raise APIError(1, "foobar")

        dynamic_phabricator_getattr.side_effect = side_effect

        with pytest.raises(PhabfiveRemoteException):
            p = Phabfive()
