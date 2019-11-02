# python std lib
import os

# phabfive imports
from phabricator import APIError, Phabricator
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.paste import Paste

# 3rd party imports
import mock
import pytest
from mock import patch, Mock


class MockEditResource():
    def __init__(self, wanted_response):
        self.wanted_response = wanted_response

    def edit(self, *args, **kwargs):
        self.edit_args = args
        self.edit_kwargs = kwargs

        return self.wanted_response


class MockPhabricator():

    def __init__(self, wanted_response, *args, **kwargs):
        self.wanted_response = wanted_response
        self.paste = MockEditResource(self.wanted_response)


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


def test_create_paste_api_error_on_edit():
    """
    When inputting valid arguments but the backend sends up APIError we should get a wrapped
    PhabfiveDataException raised back up to us.
    """
    with patch.object(Phabricator, "__call__", autospec=True) as dynamic_phabricator_call:
        def side_effect(self, *args, **kwargs):
            print("inside", args, kwargs)
            raise APIError(1, "foobar")

        dynamic_phabricator_call.side_effect = side_effect

        with pytest.raises(PhabfiveDataException):
            p = Paste()
            p.create_paste(
                title="title_foo",
                file=None,
                language="lang_foo",
                subscribers="subs_foo",
            )


def test_create_paste_invalid_file_error():
    """
    If we input a file path that do not compute to a file on disk we should check
    for the normal FileNotFoundError python exception.
    """
    with pytest.raises(FileNotFoundError):
        p = Paste()
        p.create_paste(
            title='title_foo',
            file='random_foobar_file.txt',
            language='lang_foo',
            subscribers='subs_foo',
        )


def test_create_paste_transaction_values():
    """
    When inputting all valid data we need to check that transactions values is as expected and
    built up propelry to all data that we need.
    """
    p = Paste()
    p.phab = MockPhabricator({'object': 'foobar'})

    result = p.create_paste(
        title="title_foo",
        file=None,
        language="lang_foo",
        subscribers="subs_foo",
    )
    assert result == 'foobar'
    print(p.phab.paste.edit_args)
    print(p.phab.paste.edit_kwargs)

    expected_transactions = {
      "transactions": [
        {
          "type": "title",
          "value": "title_foo"
        },
        {
          "type": "language",
          "value": "lang_foo"
        },
        {
          "type": "projects.add",
          "value": []
        },
        {
          "type": "subscribers.add",
          "value": "subs_foo"
        }
      ]
    }
