# -*- coding: utf-8 -*-

# python std lib
import json
import logging
import re

# phabfive imports
from phabfive.constants import MONOGRAMS
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException, PhabfiveRemoteException

# 3rd party imports
from phabricator import APIError


log = logging.getLogger(__name__)


class Passphrase(Phabfive):
    def __init__(self):
        super(Passphrase, self).__init__()

    def _validate_identifier(self, id_):
        return re.match(f"^{MONOGRAMS['passphrase']}$", id_)

    def get_passphrase(self, id_str):
        """Retrieve passphrase data by ID.

        Parameters
        ----------
        id_str : str
            Passphrase ID (e.g., "K123")

        Returns
        -------
        dict
            Structured passphrase data with keys:
            - id: str (e.g., "K1")
            - url: str (full URL to passphrase)
            - _link: Rich Text (OSC8 hyperlink for rich/tree formats)
            - type: str (Password, Token, SSH Key, Note)
            - name: str (credential name)
            - username: str or None (only for Password type)
            - secret: str (the actual secret value)

        Raises
        ------
        PhabfiveDataException
            If ID is invalid, no data found, or access denied
        PhabfiveRemoteException
            If API call fails
        """
        if not self._validate_identifier(id_str):
            raise PhabfiveDataException(
                f"Invalid passphrase ID '{id_str}'. Expected format: K123"
            )

        numeric_id = id_str.replace("K", "")

        try:
            response = self.phab.passphrase.query(
                ids=[numeric_id],
                needSecrets=1,
            )
        except APIError as e:
            raise PhabfiveRemoteException(e)

        has_data = response.get("data", {})

        if not has_data:
            raise PhabfiveDataException(f"K{numeric_id} has no data or other error")

        log.debug(json.dumps(response["data"], indent=2))

        # Get the first (and only) entry
        data = next(iter(response["data"].values()))

        # Check for API access denial
        material = data.get("material", {})

        # Handle material being a list (empty) or dict
        if isinstance(material, list):
            material = {}

        if "noAPIAccess" in material:
            raise PhabfiveDataException(
                f"Access denied, visit {self.url}/K{numeric_id} to allow Conduit",
            )

        # Map API type to display type
        type_map = {
            "password": "Password",
            "token": "Token",
            "ssh-generated-key": "SSH Key",
            "ssh-key-text": "SSH Key",
            "note": "Note",
        }

        # Extract secret from material (different key per type)
        secret = (
            material.get("password")  # nosec-B105
            or material.get("token")
            or material.get("privateKey")
            or material.get("note")
            or ""
        )

        monogram = f"K{data['id']}"
        url = f"{self.url}/{monogram}"
        credential_type = data.get("type", "")

        return {
            "id": monogram,
            "url": url,
            "_link": self.format_link(url, monogram),
            "type": type_map.get(credential_type, credential_type or "Unknown"),
            "name": data.get("name", ""),
            "username": data.get("username"),
            "secret": secret,
        }

    def get_secret(self, ids):
        """Retrieve only the secret value.

        Parameters
        ----------
        ids : str
            Passphrase ID (e.g., "K123")

        Returns
        -------
        str
            The secret value
        """
        data = self.get_passphrase(ids)
        return data["secret"]
