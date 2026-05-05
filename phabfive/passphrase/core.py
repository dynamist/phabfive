# -*- coding: utf-8 -*-
"""Core Passphrase class for credential management."""

import json
import logging
import re

from phabricator import APIError

from phabfive.constants import MONOGRAMS
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException, PhabfiveRemoteException

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

    def _get_credential_dates(self, phid):
        """Get creation and modification dates from transaction history.

        Parameters
        ----------
        phid : str
            The PHID of the credential

        Returns
        -------
        tuple
            (dateCreated, dateModified) as Unix timestamps, or (None, None)
        """
        try:
            response = self.phab.transaction.search(objectIdentifier=phid)
            transactions = response.get("data", [])
            if not transactions:
                return (None, None)

            # Get min dateCreated (oldest transaction = creation time)
            # Get max dateModified (most recent modification)
            created = min(t.get("dateCreated", 0) for t in transactions)
            modified = max(t.get("dateModified", 0) for t in transactions)
            return (created if created else None, modified if modified else None)
        except (APIError, Exception):
            return (None, None)

    def _format_credential(self, data, need_secrets=True, need_public_keys=False):
        """Format a single credential from API response.

        Parameters
        ----------
        data : dict
            Raw credential data from API
        need_secrets : bool
            Whether to include secret material
        need_public_keys : bool
            Whether to include public keys for SSH credentials

        Returns
        -------
        dict
            Formatted credential data
        """
        # Map API type to display type
        type_map = {
            "password": "Password",
            "token": "Token",
            "ssh-generated-key": "SSH Key",
            "ssh-key-text": "SSH Key",
            "note": "Note",
        }

        material = data.get("material", {})
        if isinstance(material, list):
            material = {}

        # Extract secret from material (different key per type)
        secret = None
        if need_secrets:
            if "noAPIAccess" in material:
                secret = "[Access denied - enable Conduit access in web UI]"
            else:
                secret = (
                    material.get("password")  # nosec-B105
                    or material.get("token")
                    or material.get("privateKey")
                    or material.get("note")
                    or ""
                )

        # Extract public key for SSH credentials
        public_key = None
        if need_public_keys and data.get("type") in (
            "ssh-generated-key",
            "ssh-key-text",
        ):
            public_key = material.get("publicKey")

        monogram = f"K{data['id']}"
        url = f"{self.url}/{monogram}"
        credential_type = data.get("type", "")

        result = {
            "id": monogram,
            "url": url,
            "_link": self.format_link(url, monogram),
            "type": type_map.get(credential_type, credential_type or "Unknown"),
            "name": data.get("name", ""),
            "username": data.get("username"),
        }

        if need_secrets:
            result["secret"] = secret

        if need_public_keys and public_key:
            result["public_key"] = public_key

        # Get dates from transaction history
        phid = data.get("phid")
        if phid:
            date_created, date_modified = self._get_credential_dates(phid)
            if date_created:
                result["dateCreated"] = date_created
            if date_modified:
                result["dateModified"] = date_modified

        return result

    def search_passphrases(
        self, query=None, credential_type=None, need_secrets=False, limit=100
    ):
        """Search/list all accessible credentials.

        Parameters
        ----------
        query : str, optional
            Filter by name (case-insensitive partial match)
        credential_type : str, optional
            Filter by type: password, token, key, note
        need_secrets : bool
            Include secret material (default: False for security)
        limit : int
            Maximum number of results

        Returns
        -------
        list
            List of credential dictionaries
        """
        try:
            response = self.phab.passphrase.query(
                needSecrets=1 if need_secrets else 0,
                limit=limit,
            )
        except APIError as e:
            raise PhabfiveRemoteException(e)

        credentials = []
        data = response.get("data", {})

        # Map user-friendly type names to API types
        type_filter_map = {
            "password": ["password"],
            "token": ["token"],
            "key": ["ssh-generated-key", "ssh-key-text"],
            "ssh": ["ssh-generated-key", "ssh-key-text"],
            "note": ["note"],
        }

        for item in data.values():
            # Apply type filter
            if credential_type:
                api_types = type_filter_map.get(credential_type.lower(), [])
                if api_types and item.get("type") not in api_types:
                    continue

            # Apply name filter (case-insensitive partial match)
            if query:
                name = item.get("name", "")
                if query.lower() not in name.lower():
                    continue

            credentials.append(self._format_credential(item, need_secrets=need_secrets))

        return credentials

    def get_passphrases(self, id_list, need_secrets=True, need_public_keys=False):
        """Retrieve multiple passphrases by ID.

        Parameters
        ----------
        id_list : list
            List of passphrase IDs (e.g., ["K1", "K2", "K3"])
        need_secrets : bool
            Include secret material
        need_public_keys : bool
            Include public keys for SSH credentials

        Returns
        -------
        list
            List of credential dictionaries

        Raises
        ------
        PhabfiveDataException
            If any ID is invalid or not found
        """
        # Validate all IDs first
        numeric_ids = []
        for id_str in id_list:
            id_str = id_str.strip()
            if not self._validate_identifier(id_str):
                raise PhabfiveDataException(
                    f"Invalid passphrase ID '{id_str}'. Expected format: K123"
                )
            numeric_ids.append(id_str.replace("K", ""))

        try:
            response = self.phab.passphrase.query(
                ids=numeric_ids,
                needSecrets=1 if need_secrets else 0,
                needPublicKeys=1 if need_public_keys else 0,
            )
        except APIError as e:
            raise PhabfiveRemoteException(e)

        data = response.get("data", {})

        if not data:
            raise PhabfiveDataException("No credentials found for the specified IDs")

        credentials = []
        # Preserve order of requested IDs
        for numeric_id in numeric_ids:
            found = False
            for item in data.values():
                if str(item.get("id")) == numeric_id:
                    credentials.append(
                        self._format_credential(
                            item,
                            need_secrets=need_secrets,
                            need_public_keys=need_public_keys,
                        )
                    )
                    found = True
                    break
            if not found:
                raise PhabfiveDataException(f"K{numeric_id} not found or access denied")

        return credentials
