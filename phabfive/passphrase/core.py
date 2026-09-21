# -*- coding: utf-8 -*-
"""Core Passphrase class for credential management."""

import json
import logging
import re


from phabfive.constants import MONOGRAMS
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveRemoteException,
)
from phabfive.pagination import iter_pages

log = logging.getLogger(__name__)

# Map user-friendly credential type names to API types
CREDENTIAL_TYPE_FILTERS = {
    "password": ["password"],
    "token": ["token"],
    "key": ["ssh-generated-key", "ssh-key-text"],
    "ssh": ["ssh-generated-key", "ssh-key-text"],
    "note": ["note"],
}


class Passphrase(Phabfive):
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
        except PhabfiveAPIException as e:
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

    def get_credential_record(self, id_str):
        """Fetch a credential's identity without reading its secret.

        Attaching a credential to something only needs its PHID and type.
        Phabricator holds the material and uses it server side, so asking
        Conduit for the secret as well would read a private key for no
        reason - and fail on a credential whose owner never granted Conduit
        access to it, which has nothing to do with whether the credential
        can be attached.

        Parameters
        ----------
        id_str : str
            Passphrase ID (e.g., "K123")

        Returns
        -------
        dict
            The raw credential record, with 'phid', 'type' and 'monogram'

        Raises
        ------
        PhabfiveDataException
            If the ID is invalid or no such credential exists
        PhabfiveRemoteException
            If the API call fails
        """
        if not self._validate_identifier(id_str):
            raise PhabfiveDataException(
                f"Invalid passphrase ID '{id_str}'. Expected format: K123"
            )

        numeric_id = id_str.replace("K", "")

        try:
            response = self.phab.passphrase.query(
                ids=[numeric_id],
                needSecrets=0,
            )
        except PhabfiveAPIException as e:
            raise PhabfiveRemoteException(e)

        data = response.get("data", {})

        if not data:
            raise PhabfiveDataException(f"K{numeric_id} has no data or other error")

        return next(iter(data.values()))

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
        except Exception:
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
        self, query=None, credential_type=None, need_secrets=False, limit=None
    ):
        """Search/list all accessible credentials.

        Follows the result cursor, so an instance with more than the 100
        credentials one page holds is not answered short.

        The type and name filters are applied in Python, which is why `limit`
        is counted here rather than sent to the API: sent, it would truncate
        every credential before a single one had been tested, so `--limit 2
        --type key` could report none of the keys that exist. Pages are read
        one at a time and reading stops as soon as `limit` credentials match,
        so a small limit still costs a small number of requests.

        Parameters
        ----------
        query : str, optional
            Filter by name (case-insensitive partial match)
        credential_type : str, optional
            Filter by type: password, token, key, note
        need_secrets : bool
            Include secret material (default: False for security)
        limit : int, optional
            Maximum number of matching credentials to return. None means
            every match.

        Returns
        -------
        list
            List of credential dictionaries

        Raises
        ------
        PhabfiveConfigException
            If credential_type is not a known type
        """
        api_types = None
        if credential_type:
            api_types = CREDENTIAL_TYPE_FILTERS.get(credential_type.lower())
            if api_types is None:
                raise PhabfiveConfigException(
                    f"Invalid type '{credential_type}'. "
                    f"Valid choices: {', '.join(CREDENTIAL_TYPE_FILTERS)}"
                )

        credentials = []

        try:
            pages = iter_pages(
                self.phab.passphrase.query,
                needSecrets=1 if need_secrets else 0,
            )

            for page in pages:
                for item in page:
                    # Apply type filter
                    if api_types and item.get("type") not in api_types:
                        continue

                    # Apply name filter (case-insensitive partial match)
                    if query:
                        name = item.get("name", "")
                        if query.lower() not in name.lower():
                            continue

                    credentials.append(
                        self._format_credential(item, need_secrets=need_secrets)
                    )

                    if limit is not None and len(credentials) >= limit:
                        # Closed here rather than left to the collector, so
                        # the generator cannot be resumed into another page
                        pages.close()

                        return credentials
        except PhabfiveAPIException as e:
            raise PhabfiveRemoteException(e)

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
        except PhabfiveAPIException as e:
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


__all__ = ["Passphrase"]
