# -*- coding: utf-8 -*-

# python std lib
import json
import logging
import os
import stat
from urllib.parse import urlparse

# 3rd party imports
from phabricator import APIError, Phabricator

# phabfive imports
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException, PhabfiveRemoteException

log = logging.getLogger(__name__)


class User(Phabfive):
    def __init__(self):
        super(User, self).__init__()

    def whoami(self):
        """Return filtered user info dict with userName, realName, primaryEmail, uri."""
        try:
            response = self.phab.user.whoami()
        except APIError as e:
            raise PhabfiveRemoteException(e)

        return {
            key: value
            for (key, value) in response.items()
            if key in ["userName", "realName", "primaryEmail", "uri"]
        }

    def whoami_configured_host(self):
        """Run whoami against the host phabfive is configured to use.

        Uses the PHAB_URL/PHAB_TOKEN that came out of the normal
        configuration chain, so it reports the host every other phabfive
        command talks to.

        Returns
        -------
        dict
            Same shape as one entry of :meth:`whoami_all_hosts`.
        """
        return self._whoami_for_host(
            self._normalize_url(self.conf["PHAB_URL"]),
            self.conf.get("PHAB_TOKEN"),
            phab=self.phab,
        )

    def _whoami_for_host(self, normalized_url, token, phab=None):
        """Run whoami against a single host and format the result.

        Parameters
        ----------
        normalized_url : str
            Full API URL, e.g. "https://phorge.example.com/api/".
        token : str or None
            API token for this host.
        phab : Phabricator, optional
            Existing client to reuse. A new one is built when omitted.

        Returns
        -------
        dict
            With "Host", "URL" and either "User" (plus "_link") or "Error".
        """
        parsed = urlparse(normalized_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        result = {
            "Host": parsed.netloc,
            "URL": normalized_url,
            "_base_url": base_url,
        }

        if not token:
            result["Error"] = "No token configured for this host"
            return result

        try:
            if phab is None:
                phab = Phabricator(host=normalized_url, token=token)
                phab.update_interfaces()
            response = phab.user.whoami()

            user_name = response.get("userName", "")

            result["User"] = {
                "UserName": user_name,
                "RealName": response.get("realName", ""),
                "PrimaryEmail": response.get("primaryEmail", ""),
            }

            # Add _link for rich format (clickable hyperlink)
            result["_link"] = self.format_link(
                f"{base_url}/p/{user_name}/", user_name, show_url=False
            )
        except APIError as e:
            result["Error"] = str(e).replace("ERR-CONDUIT-CORE: ", "")
        except Exception as e:
            result["Error"] = str(e)

        return result

    def whoami_all_hosts(self):
        """Run whoami against all hosts in ~/.arcrc.

        Returns
        -------
        list[dict]
            List of user info dicts, one per host. Each contains:
            - Host: FQDN only (e.g., "dynamist.phacility.com")
            - URL: Full API URL for PHAB_URL (e.g., "https://dynamist.phacility.com/api/")
            - User: dict with UserName, RealName, PrimaryEmail, Link
            - _link: Rich hyperlink to user profile (for rich format)
            - Error: error message if whoami failed for this host (optional)
        """
        arcrc_path = os.path.expanduser("~/.arcrc")

        if not os.path.exists(arcrc_path):
            raise PhabfiveConfigException(f"No .arcrc file found at {arcrc_path}")

        # Security check: ensure file has secure permissions
        self._check_arcrc_permissions(arcrc_path)

        try:
            with open(arcrc_path, "r") as f:
                arcrc_data = json.load(f)
        except json.JSONDecodeError as e:
            raise PhabfiveConfigException(f"Failed to parse {arcrc_path}: {e}")
        except IOError as e:
            raise PhabfiveConfigException(f"Failed to read {arcrc_path}: {e}")

        hosts = arcrc_data.get("hosts", {})

        if not hosts:
            raise PhabfiveConfigException("No hosts found in ~/.arcrc")

        results = []

        for host_uri, host_data in hosts.items():
            results.append(
                self._whoami_for_host(
                    self._normalize_url(host_uri), host_data.get("token")
                )
            )

        return results

    def _check_arcrc_permissions(self, file_path):
        """Check that ~/.arcrc has secure permissions.

        Note: This check is skipped on Windows.
        """
        # Skip permission check on Windows
        if os.name == "nt":
            return

        if not os.path.exists(file_path):
            return

        file_stat = os.stat(file_path)
        mode = file_stat.st_mode

        # Check if group or others have any permissions
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            actual_perms = oct(mode & 0o777)
            raise PhabfiveConfigException(
                f"{file_path} has insecure permissions ({actual_perms}). "
                "The file contains sensitive credentials and should only be readable by you. "
                f"Please run: chmod 600 {file_path}"
            )
