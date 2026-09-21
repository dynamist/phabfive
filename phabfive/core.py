# -*- coding: utf-8 -*-

# python std lib
import copy
import glob
import json
import logging
import os
import re
import stat
import sys
from urllib.parse import urlparse

# phabfive imports
from phabfive.conduit import Conduit
from phabfive.constants import (
    FORMAT_ALIASES,
    REQUIRED,
    MISSING_CONFIG_HINTS,
    VALIDATORS,
    VALIDATION_HINTS,
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
    _fallback_format = "yaml"  # Format used when stdout is not a TTY
    # Maximum line width for rich format (to prevent YAML breaking)
    MAX_LINE_WIDTH = 4096

    # Monogram patterns for object type detection
    TASK_PATTERN = re.compile(r"[Tt](\d+)")
    PASSPHRASE_PATTERN = re.compile(r"[Kk](\d+)")
    PASTE_PATTERN = re.compile(r"[Pp](\d+)")

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

    @classmethod
    def _get_auto_format(cls):
        """Determine default output format based on whether stdout is a TTY.

        Returns:
            str: "rich" if stdout is a terminal, PHAB_FALLBACK if piped/redirected
        """
        import sys

        # If output is piped or redirected, use fallback format for machine processing
        if not sys.stdout.isatty():
            return cls._fallback_format

        # If output is to a terminal, use rich format for human readability
        return "rich"

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
                    f"(length: {len(line)}). Use --format=yaml for guaranteed valid YAML output."
                )

    def __init__(
        self, url=None, token=None, *, config=None, verify=False, select_host=None
    ):
        """Configure an instance and, if asked, check the connection.

        With no arguments the configuration is discovered the way the command
        discovers it - /etc and ~/.config yaml, `.arcconfig` in the git root,
        `~/.arcrc` and the environment; see `read_config`.

        Passing any of `url`, `token` or `config` configures the instance
        explicitly instead, and nothing is discovered: the hard-coded defaults,
        then `config`, then `url` and `token`. A program that knows where it is
        talking to is then independent of whoever runs it and wherever they
        run it from.

        Parameters
        ----------
        url : str, optional
            The instance, e.g. ``https://phorge.example.com``. The ``/api/``
            suffix is added when missing.
        token : str, optional
            A Conduit API token.
        config : dict, optional
            Any of the CONFIGURABLES keys, e.g. ``{"PHAB_SPACE": "S2"}``.
        verify : bool
            Check the connection now with a ``user.whoami`` call. Off by
            default, so constructing makes no request at all: the client is
            built on first use, and a bad token surfaces from the first call.
        select_host : callable, optional
            Called with the list of hosts when ``~/.arcrc`` holds several and
            nothing says which to use. Returns the chosen one. Only consulted
            when discovering.

        Raises
        ------
        PhabfiveConfigException
            When a required value is missing or malformed.
        """
        if url is None and token is None and config is None:
            self.conf = self.load_config(select_host=select_host)
        else:
            self.conf = self._explicit_config(url, token, config)
            self._explicit_phab_url = bool(self.conf.get("PHAB_URL"))

        maxlen = 8 + len(max(dict(self.conf).keys(), key=len))

        for key, value in dict(self.conf).items():
            dots = "." * (maxlen - len(key))
            log.debug(f"{key} {dots} {value}")

        # check for required configurables
        for conf_key, conf_value in dict(self.conf).items():
            if conf_key in REQUIRED and not conf_value:
                error = f"{conf_key} is not configured"
                example = MISSING_CONFIG_HINTS.get(conf_key)

                if example:
                    error += ", " + example

                raise PhabfiveConfigException(error)

        # check validity of configurables
        for validator_key in VALIDATORS.keys():
            if not re.match(VALIDATORS[validator_key], self.conf[validator_key]):
                error = f"{validator_key} is malformed"
                example = VALIDATION_HINTS.get(validator_key)

                if example:
                    error += ", " + example

                raise PhabfiveConfigException(error)

        # Set fallback format from config (used when stdout is not a TTY).
        # Resolve aliases here so the rest of the program only ever sees a
        # real OutputFormat value, exactly as --format=ndjson is resolved.
        fallback = self.conf.get("PHAB_FALLBACK", "yaml")
        Phabfive._fallback_format = FORMAT_ALIASES.get(fallback, fallback)

        self.phab = Conduit(self._client_factory())

        url = urlparse(self.conf["PHAB_URL"])

        self.url = f"{url.scheme}://{url.netloc}"

        if verify:
            self.verify_connection()

    @classmethod
    def _explicit_config(cls, url, token, config):
        """Build a configuration from arguments alone, discovering nothing."""
        config = dict(config or {})

        unknown = sorted(set(config) - set(CONFIGURABLES))
        if unknown:
            raise PhabfiveConfigException(
                f"Unknown configuration key(s): {', '.join(unknown)}. "
                f"Known keys: {', '.join(CONFIGURABLES)}"
            )

        conf = copy.deepcopy(DEFAULTS)
        conf.update(config)
        if url is not None:
            conf["PHAB_URL"] = url
        if token is not None:
            conf["PHAB_TOKEN"] = token

        # Forgive the one thing everybody gets wrong: the instance's address
        # rather than its API endpoint.
        if conf["PHAB_URL"]:
            conf["PHAB_URL"] = cls._normalize_url(conf["PHAB_URL"])

        return conf

    @classmethod
    def _from_parent(cls, parent):
        """Make an instance that shares `parent`'s configuration and client.

        For an app that needs another app's methods - Diffusion reading
        credentials through Passphrase, Edit editing through Maniphest.
        Constructing the sibling would read the configuration again, and
        build and connect a second client to the same host.
        """
        child = cls.__new__(cls)
        for name in ("conf", "url", "phab", "_explicit_phab_url"):
            if hasattr(parent, name):
                setattr(child, name, getattr(parent, name))
        return child

    def _client_factory(self):
        """Return the recipe for this instance's Conduit client.

        `Phabricator` is looked up in this module when the client is built,
        not when the recipe is made, so a test that patches
        `phabfive.core.Phabricator` still reaches every instance.
        """
        host = self._normalize_url(self.conf.get("PHAB_URL"))
        token = self.conf.get("PHAB_TOKEN")

        def build():
            return Phabricator(host=host, token=token)

        return build

    def verify_connection(self):
        """ """
        try:
            self.phab.user.whoami()
        except APIError as e:
            raise PhabfiveRemoteException(e)

    @classmethod
    def _check_secure_permissions(cls, file_path):
        """
        Check that a file has secure permissions (not readable by group/others).

        Note: This check is skipped on Windows, which uses ACLs instead of
        Unix-style permissions.

        Parameters
        ----------
        file_path : str
            Path to the file to check

        Raises
        ------
        PhabfiveConfigException
            If the file has insecure permissions (group or others can read)
        """
        # Skip permission check on Windows (uses ACLs, not Unix permissions)
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
                "The file may contain sensitive credentials and should only be readable by you. "
                f"Please run: chmod 600 {file_path}"
            )

    @classmethod
    def _load_arcrc(cls, current_conf, select_host=None):
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
        cls._check_secure_permissions(arcrc_path)

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
            normalized_current = cls._normalize_url(current_url)

            for host_uri, host_data in hosts.items():
                normalized_host = cls._normalize_url(host_uri)
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
                    normalized_default = cls._normalize_url(default_host)

                    for host_uri, host_data in hosts.items():
                        normalized_host = cls._normalize_url(host_uri)
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
                    # No default, or it matched nothing: the caller may choose,
                    # and one that cannot - no callback, or a callback that
                    # declines by returning None - gets told how to decide.
                    selected = select_host(list(hosts)) if select_host else None
                    if selected is None:
                        host_list = "\n  - ".join(hosts.keys())
                        raise PhabfiveConfigException(
                            f"Multiple hosts found in ~/.arcrc but PHAB_URL is not configured. "
                            f"Please set PHAB_URL to specify which host to use:\n  - {host_list}\n\n"
                            f"Example: export PHAB_URL=https://phorge.example.com/api/"
                        )
                    result["PHAB_URL"] = selected
                    token = hosts[selected].get("token")
                    if token:
                        result["PHAB_TOKEN"] = token

        return result

    @classmethod
    def _load_arcconfig(cls):
        """
        Load PHAB_URL from .arcconfig in the git repository root.

        Walks up from the current working directory looking for a .git directory,
        then reads .arcconfig in that directory. Extracts `phabricator.uri` and
        normalizes it to an API URL.

        Returns
        -------
        dict
            Configuration dict with PHAB_URL if found, empty dict otherwise
        """
        # Walk up from cwd looking for .git directory
        current = os.getcwd()
        git_root = None

        while True:
            if os.path.isdir(os.path.join(current, ".git")):
                git_root = current
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        if git_root is None:
            log.debug("Not in a git repository, skipping .arcconfig")
            return {}

        arcconfig_path = os.path.join(git_root, ".arcconfig")

        if not os.path.exists(arcconfig_path):
            log.debug(f"No .arcconfig found at {arcconfig_path}")
            return {}

        log.debug(f"Loading configuration from {arcconfig_path}")

        try:
            with open(arcconfig_path, "r") as f:
                arcconfig_data = json.load(f)
        except json.JSONDecodeError as e:
            log.warning(f"Failed to parse {arcconfig_path}: {e}")
            return {}
        except IOError as e:
            log.warning(f"Failed to read {arcconfig_path}: {e}")
            return {}

        uri = arcconfig_data.get("phabricator.uri")

        if not uri:
            log.debug("No phabricator.uri found in .arcconfig")
            return {}

        normalized = cls._normalize_url(uri)
        log.debug(f"Using PHAB_URL from .arcconfig: {normalized}")
        return {"PHAB_URL": normalized}

    def load_config(self, select_host=None):
        """
        Load configuration and remember whether PHAB_URL was chosen explicitly.

        See read_config for the search order.
        """
        conf, explicit_phab_url = type(self).read_config(select_host=select_host)
        self._explicit_phab_url = explicit_phab_url
        return conf

    @classmethod
    def read_config(cls, select_host=None):
        """
        Load configuration from configuration files and environment variables.

        Search order, latest has presedence:

          1. hard coded defaults
          2. `/etc/phabfive.yaml`
          3. `/etc/phabfive.d/*.yaml`
          4. `~/.config/phabfive.yaml`
          5. `~/.config/phabfive.d/*.yaml`
          6. `.arcconfig` in git root
          7. `~/.arcrc` (Arcanist configuration)
          8. environment variables

        A classmethod because the cache needs PHAB_URL to key its entries
        before it knows whether it will ask the server anything, and a
        Phabfive() validates a whole configuration it does not need.

        Parameters
        ----------
        select_host : callable, optional
            Called with the list of hosts when ~/.arcrc holds several, no
            PHAB_URL is configured and ~/.arcrc names no default. Returns the
            chosen host.

        Returns
        -------
        tuple
            (conf, explicit_phab_url), where explicit_phab_url is whether the
            host came from anything other than ~/.arcrc
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

        # Track config state before user yaml files to detect deprecated keys
        pre_user_url = conf.get("PHAB_URL", "")
        pre_user_token = conf.get("PHAB_TOKEN", "")

        user_conf_file = os.path.join(f"{appdirs.user_config_dir('phabfive')}.yaml")
        log.debug(f"Loading configuration file: {user_conf_file}")
        cls._check_secure_permissions(user_conf_file)
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
            cls._check_secure_permissions(conf_file)
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(anyconfig.multi_load(user_conf_dir)).items()
                if key in CONFIGURABLES
            },
        )

        # Warn if PHAB_URL or PHAB_TOKEN were set from user yaml config files
        deprecated_keys = []
        if conf.get("PHAB_URL", "") != pre_user_url and conf.get("PHAB_URL", ""):
            deprecated_keys.append("PHAB_URL")
        if conf.get("PHAB_TOKEN", "") != pre_user_token and conf.get("PHAB_TOKEN", ""):
            deprecated_keys.append("PHAB_TOKEN")
        if deprecated_keys:
            keys_str = "/".join(deprecated_keys)
            print(
                f"WARNING: ~/.config/phabfive.yaml contains {keys_str} which is deprecated. "
                "Migrate credentials to ~/.arcrc using: phabfive user setup",
                file=sys.stderr,
            )

        # Load from .arcconfig in git repository root
        arcconfig_conf = cls._load_arcconfig()
        if arcconfig_conf:
            log.debug("Merging configuration from .arcconfig")
            anyconfig.merge(conf, arcconfig_conf)

        # Remember whether a host was chosen by anything other than ~/.arcrc.
        # Everything merged so far (site and user yaml, .arcconfig) plus the
        # environment means the user pointed phabfive at a specific host; a
        # PHAB_URL that only comes out of ~/.arcrc does not.
        explicit_phab_url = bool(conf.get("PHAB_URL")) or bool(environ.get("PHAB_URL"))

        # Load from Arcanist .arcrc file (supports single or multiple hosts)
        # Include PHAB_URL from environment so .arcrc can match the right host
        # even though env vars are formally merged later
        arcrc_lookup_conf = dict(conf)
        if "PHAB_URL" in environ and environ["PHAB_URL"]:
            arcrc_lookup_conf["PHAB_URL"] = environ["PHAB_URL"]
        arcrc_conf = cls._load_arcrc(arcrc_lookup_conf, select_host=select_host)
        if arcrc_conf:
            log.debug("Merging configuration from ~/.arcrc")
            anyconfig.merge(conf, arcrc_conf)

        log.debug("Loading configuration from environment variables")
        anyconfig.merge(
            conf,
            {key: value for key, value in environ.items() if key in CONFIGURABLES},
        )

        return conf, explicit_phab_url

    def has_explicit_phab_url(self):
        """
        Whether PHAB_URL was configured outside of ~/.arcrc.

        True when the host came from an environment variable, `.arcconfig`,
        or a phabfive yaml config, i.e. the user pointed phabfive at one
        specific host. False when the host was only discovered by reading
        ~/.arcrc, or when no host is configured at all.

        Returns
        -------
        bool
        """
        return getattr(self, "_explicit_phab_url", False)

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

    @classmethod
    def _normalize_url(cls, url):
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

    def parse_monogram(self, text):
        """Parse a monogram from text and return object type and ID.

        Args:
            text (str): Text containing a monogram (e.g., "T123", "https://phorge.example.com/T456")

        Returns:
            tuple: (object_type, object_id) where object_type is "task"|"passphrase"|"paste"
                   and object_id is the numeric ID as string

        Raises:
            ValueError: If no valid monogram is found
        """
        # Try task monogram
        match = self.TASK_PATTERN.search(text)
        if match:
            return ("task", match.group(1))

        # Try passphrase monogram
        match = self.PASSPHRASE_PATTERN.search(text)
        if match:
            return ("passphrase", match.group(1))

        # Try paste monogram
        match = self.PASTE_PATTERN.search(text)
        if match:
            return ("paste", match.group(1))

        raise ValueError(f"No valid monogram found in: {text}")

    def parse_object_ids(self, object_id_str):
        """Parse object ID string which may contain comma-separated IDs.

        Args:
            object_id_str (str): Single ID or comma-separated IDs (e.g., "T123" or "T123,T124,T125")

        Returns:
            list: List of (object_type, object_id) tuples

        Raises:
            ValueError: If any ID is invalid or types are mixed
        """
        # Split by comma and strip whitespace
        ids = [s.strip() for s in object_id_str.split(",") if s.strip()]

        if not ids:
            raise ValueError("No valid object IDs provided")

        results = []
        seen_types = set()

        for id_str in ids:
            obj_type, obj_id = self.parse_monogram(id_str)
            results.append((obj_type, obj_id))
            seen_types.add(obj_type)

        # Check for mixed types
        if len(seen_types) > 1:
            raise ValueError(
                f"Cannot mix object types in a single edit command: {seen_types}"
            )

        return results


__all__ = ["Phabfive"]
