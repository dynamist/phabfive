# -*- coding: utf-8 -*-

# python std lib
import copy
import glob
import json
import logging
import os
import re
import stat
from urllib.parse import urlparse

# phabfive imports
from phabfive.constants import (
    REQUIRED,
    CONFIG_EXAMPLES,
    VALIDATORS,
    VALID_EXAMPLES,
    DEFAULTS,
    CONFIGURABLES,
)
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveRemoteException,
)

# 3rd party imports
import anyconfig
import appdirs
from phabricator import Phabricator, APIError
from rich.console import Console
from rich.text import Text


log = logging.getLogger(__name__)
logging.getLogger("anyconfig").setLevel(logging.ERROR)


class Phabfive:
    # Output formatting options (set by CLI)
    _ascii_when = "auto"
    _hyperlink_when = "auto"
    _output_format = "rich"
    # Maximum line width for rich format (to prevent YAML breaking)
    MAX_LINE_WIDTH = 4096

    @classmethod
    def set_output_options(
        cls, ascii_when="auto", hyperlink_when="auto", output_format="rich"
    ):
        """Set global output formatting options."""
        cls._ascii_when = ascii_when
        cls._hyperlink_when = hyperlink_when
        cls._output_format = output_format

    @staticmethod
    def _should_use_ascii():
        """Determine if ASCII mode should be used based on terminal capabilities."""
        import sys
        import locale

        # Check if stdout is a TTY
        if not sys.stdout.isatty():
            return True

        # Check locale encoding
        try:
            encoding = locale.getpreferredencoding(False).lower()
            if "utf" not in encoding:
                return True
        except Exception:
            return True

        return False

    @staticmethod
    def _should_use_hyperlink():
        """Determine if hyperlinks should be used based on terminal capabilities.

        There's no standard query for OSC 8 support, so we check for known
        supporting terminals via environment variables.
        """
        import sys
        import os

        # Must be a TTY
        if not sys.stdout.isatty():
            return False

        # Check for terminals known to support OSC 8
        term = os.environ.get("TERM", "")
        term_program = os.environ.get("TERM_PROGRAM", "")
        colorterm = os.environ.get("COLORTERM", "")

        # Known supporting terminal programs
        if term_program in (
            "iTerm.app",
            "WezTerm",
            "vscode",
            "Hyper",
            "mintty",
            "ghostty",
        ):
            return True

        # Windows Terminal
        if os.environ.get("WT_SESSION"):
            return True

        # VTE-based terminals (GNOME Terminal, Tilix, Terminator, etc.)
        # VTE >= 0.50 supports OSC 8 (version 5000+)
        vte_version = os.environ.get("VTE_VERSION", "")
        if vte_version.isdigit() and int(vte_version) >= 5000:
            return True

        # KDE Konsole (version 22.04+ has good OSC 8 support)
        if os.environ.get("KONSOLE_VERSION"):
            return True

        # Terminals identifiable by TERM
        if any(t in term for t in ("kitty", "alacritty", "foot", "contour")):
            return True

        # COLORTERM=truecolor is a reasonable proxy for modern terminals
        if colorterm in ("truecolor", "24bit"):
            return True

        return False

    def _is_ascii_enabled(self):
        """Check if ASCII mode is currently enabled."""
        if self._ascii_when == "always":
            return True
        if self._ascii_when == "auto":
            return self._should_use_ascii()
        return False

    def _is_hyperlink_enabled(self):
        """Check if hyperlink mode is currently enabled."""
        if self._hyperlink_when == "always":
            return True
        if self._hyperlink_when == "auto":
            return self._should_use_hyperlink()
        return False

    def format_direction(self, direction):
        """Format direction indicator based on output mode."""
        if self._is_ascii_enabled():
            mapping = {"•": "-", "↑": "^", "↓": "v", "→": ">", "←": "<"}
            return mapping.get(direction, direction)
        return direction

    def format_link(self, url, text, show_url=True):
        """Format URL as Rich Text with hyperlink if enabled.

        Parameters
        ----------
        url : str
            The URL to link to
        text : str
            The visible text for the link
        show_url : bool
            If True and hyperlinks disabled, return url. If False, return text.

        Returns
        -------
        Text or str
            Rich Text object with link styling, or plain string if disabled
        """
        if self._is_hyperlink_enabled():
            t = Text(text)
            t.stylize(f"link {url}")
            return t
        return url if show_url else text

    def get_console(self):
        """Get a Rich Console instance for output."""
        # Use our hyperlink detection to force terminal mode
        # This ensures Rich outputs hyperlinks when our detection says the terminal supports them
        force_terminal = self._is_hyperlink_enabled()
        no_color = self._ascii_when == "always"
        # Use large width to prevent soft-wrapping which breaks YAML output
        return Console(
            force_terminal=force_terminal, no_color=no_color, width=self.MAX_LINE_WIDTH
        )

    def check_line_width(self, value, field_name="field"):
        """Check if a value exceeds the maximum line width for rich format.

        Parameters
        ----------
        value : any
            The value to check (will be converted to string)
        field_name : str
            Name of the field for error messages

        Raises
        ------
        PhabfiveDataException
            If the value exceeds MAX_LINE_WIDTH and output format is 'rich'
        """
        if self._output_format != "rich":
            return  # Only applies to rich format

        str_value = str(value) if value is not None else ""
        # Check each line in case of multi-line values
        for i, line in enumerate(str_value.split("\n")):
            if len(line) > self.MAX_LINE_WIDTH:
                raise PhabfiveDataException(
                    f"{field_name} line {i + 1} exceeds maximum width of {self.MAX_LINE_WIDTH} characters "
                    f"(length: {len(line)}). Use --format=strict for guaranteed valid YAML output."
                )

    def __init__(self):
        """ """
        self.conf = self.load_config()

        maxlen = 8 + len(max(dict(self.conf).keys(), key=len))

        for key, value in dict(self.conf).items():
            dots = "." * (maxlen - len(key))
            log.debug(f"{key} {dots} {value}")

        # check for required configurables
        for conf_key, conf_value in dict(self.conf).items():
            if conf_key in REQUIRED and not conf_value:
                error = f"{conf_key} is not configured"
                example = CONFIG_EXAMPLES.get(conf_key)

                if example:
                    error += ", " + example

                raise PhabfiveConfigException(error)

        # check validity of configurables
        for validator_key in VALIDATORS.keys():
            if not re.match(VALIDATORS[validator_key], self.conf[validator_key]):
                error = f"{validator_key} is malformed"
                example = VALID_EXAMPLES.get(validator_key)

                if example:
                    error += ", " + example

                raise PhabfiveConfigException(error)

        self.phab = Phabricator(
            host=self._normalize_url(self.conf.get("PHAB_URL")),
            token=self.conf.get("PHAB_TOKEN"),
        )

        url = urlparse(self.conf["PHAB_URL"])

        self.url = f"{url.scheme}://{url.netloc}"
        # This enables extra endpoints that normally is unaccessible
        self.phab.update_interfaces()

        self.verify_connection()

    def verify_connection(self):
        """ """
        try:
            self.phab.user.whoami()
        except APIError as e:
            raise PhabfiveRemoteException(e)

    def _check_secure_permissions(self, file_path):
        """
        Check that a file has secure permissions (not readable by group/others).

        Parameters
        ----------
        file_path : str
            Path to the file to check

        Raises
        ------
        PhabfiveConfigException
            If the file has insecure permissions (group or others can read)
        """
        if not os.path.exists(file_path):
            return

        file_stat = os.stat(file_path)
        mode = file_stat.st_mode

        # Check if group or others have any permissions
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            actual_perms = oct(mode & 0o777)
            raise PhabfiveConfigException(
                f"{file_path} has insecure permissions ({actual_perms}). "
                "The file may contain sensitive credentials and should only be readable by you. "
                f"Please run: chmod 600 {file_path}"
            )

    def _load_arcrc(self, current_conf):
        """
        Load configuration from Arcanist's ~/.arcrc file.

        The .arcrc file is a JSON file with a 'hosts' section containing
        credentials for one or more Phabricator instances:

            {
                "hosts": {
                    "https://phorge.example.com/api/": {
                        "token": "cli-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                    }
                }
            }

        Parameters
        ----------
        current_conf : dict
            The current configuration (used to match PHAB_URL if set)

        Returns
        -------
        dict
            Configuration dict with PHAB_URL and/or PHAB_TOKEN if found

        Raises
        ------
        PhabfiveConfigException
            If .arcrc has insecure permissions or multiple hosts without PHAB_URL
        """
        arcrc_path = os.path.expanduser("~/.arcrc")

        if not os.path.exists(arcrc_path):
            log.debug(f"No .arcrc file found at {arcrc_path}")
            return {}

        # Security check: ensure file has secure permissions (0600 or stricter)
        self._check_secure_permissions(arcrc_path)

        log.debug(f"Loading configuration from {arcrc_path}")

        try:
            with open(arcrc_path, "r") as f:
                arcrc_data = json.load(f)
        except json.JSONDecodeError as e:
            log.warning(f"Failed to parse {arcrc_path}: {e}")
            return {}
        except IOError as e:
            log.warning(f"Failed to read {arcrc_path}: {e}")
            return {}

        hosts = arcrc_data.get("hosts", {})

        if not hosts:
            log.debug("No hosts found in .arcrc")
            return {}

        result = {}
        current_url = current_conf.get("PHAB_URL", "")

        if current_url:
            # PHAB_URL is already set, try to find matching token in .arcrc
            normalized_current = self._normalize_url(current_url)

            for host_uri, host_data in hosts.items():
                normalized_host = self._normalize_url(host_uri)
                if normalized_current == normalized_host:
                    token = host_data.get("token")
                    if token:
                        log.debug(f"Found matching token in .arcrc for {host_uri}")
                        result["PHAB_TOKEN"] = token
                    break
        else:
            # PHAB_URL not set, use .arcrc hosts
            if len(hosts) == 1:
                # Single host - use both URL and token
                host_uri, host_data = next(iter(hosts.items()))
                token = host_data.get("token")

                log.debug(f"Using single host from .arcrc: {host_uri}")
                result["PHAB_URL"] = host_uri
                if token:
                    result["PHAB_TOKEN"] = token
            else:
                # Multiple hosts - check for default in config section
                default_host = arcrc_data.get("config", {}).get("default", "")

                if default_host:
                    # Normalize the default URL and find matching host
                    normalized_default = self._normalize_url(default_host)

                    for host_uri, host_data in hosts.items():
                        normalized_host = self._normalize_url(host_uri)
                        if normalized_default == normalized_host:
                            token = host_data.get("token")
                            log.debug(f"Using default host from .arcrc: {host_uri}")
                            result["PHAB_URL"] = host_uri
                            if token:
                                result["PHAB_TOKEN"] = token
                            break
                    else:
                        # Default didn't match any host
                        log.warning(
                            f"Default host '{default_host}' in .arcrc doesn't match any configured host"
                        )

                if not result:
                    # No default or default didn't match - error with list
                    host_list = "\n  - ".join(hosts.keys())
                    raise PhabfiveConfigException(
                        f"Multiple hosts found in ~/.arcrc but PHAB_URL is not configured. "
                        f"Please set PHAB_URL to specify which host to use:\n  - {host_list}\n\n"
                        f"Example: export PHAB_URL=https://phorge.example.com/api/"
                    )

        return result

    def load_config(self):
        """
        Load configuration from configuration files and environment variables.

        Search order, latest has presedence:

          1. hard coded defaults
          2. `/etc/phabfive.yaml`
          3. `/etc/phabfive.d/*.yaml`
          4. `~/.config/phabfive.yaml`
          5. `~/.config/phabfive.d/*.yaml`
          6. `~/.arcrc` (Arcanist configuration)
          7. environment variables
        """
        environ = os.environ.copy()

        log.debug("Loading configuration defaults")
        conf = copy.deepcopy(DEFAULTS)

        os.environ["XDG_CONFIG_DIRS"] = "/etc"

        site_conf_file = os.path.join(f"{appdirs.site_config_dir('phabfive')}.yaml")
        log.debug(f"Loading configuration file: {site_conf_file}")
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(
                    anyconfig.load(
                        site_conf_file,
                        ac_ignore_missing=True,
                    )
                ).items()
                if key in CONFIGURABLES
            },
        )

        site_conf_dir = os.path.join(
            appdirs.site_config_dir("phabfive") + ".d", "*.yaml"
        )
        log.debug(f"Loading configuration files: {site_conf_dir}")
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(anyconfig.multi_load(site_conf_dir)).items()
                if key in CONFIGURABLES
            },
        )

        user_conf_file = os.path.join(f"{appdirs.user_config_dir('phabfive')}.yaml")
        log.debug(f"Loading configuration file: {user_conf_file}")
        self._check_secure_permissions(user_conf_file)
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(
                    anyconfig.load(
                        user_conf_file,
                        ac_ignore_missing=True,
                    )
                ).items()
                if key in CONFIGURABLES
            },
        )

        user_conf_dir = os.path.join(
            f"{appdirs.user_config_dir('phabfive')}.d", "*.yaml"
        )
        log.debug(f"Loading configuration files: {user_conf_dir}")
        # Check permissions on each file in the user config directory
        for conf_file in glob.glob(user_conf_dir):
            self._check_secure_permissions(conf_file)
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(anyconfig.multi_load(user_conf_dir)).items()
                if key in CONFIGURABLES
            },
        )

        # Load from Arcanist .arcrc file (supports single or multiple hosts)
        arcrc_conf = self._load_arcrc(conf)
        if arcrc_conf:
            log.debug("Merging configuration from ~/.arcrc")
            anyconfig.merge(conf, arcrc_conf)

        log.debug("Loading configuration from environment variables")
        anyconfig.merge(
            conf,
            {key: value for key, value in environ.items() if key in CONFIGURABLES},
        )

        return conf

    def to_transactions(self, data):
        """
        Converts a dict of key:value pairs into a list of valid transaction objects
        that phabricator will accept when calling endpoints like edit
        """
        result = []

        for transaction_type, transaction_value in data.items():
            result.append(
                {
                    "type": transaction_type,
                    "value": transaction_value,
                }
            )

        return result

    def _normalize_url(self, url):
        """
        Normalizes a URL by removing trailing slashes and ensuring it ends with '/api/'

        Handles various input formats:
        - https://phorge.example.com -> https://phorge.example.com/api/
        - https://phorge.example.com/ -> https://phorge.example.com/api/
        - https://phorge.example.com/api -> https://phorge.example.com/api/
        - https://phorge.example.com/api/ -> https://phorge.example.com/api/
        """
        url = url.rstrip("/")

        if not url.endswith("/api"):
            url += "/api"

        url += "/"

        return url
