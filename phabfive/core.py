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
import warnings

# phabfive imports
from phabfive.conduit import Conduit
from phabfive.retry import Pacer, RetryPolicy
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
    PhabfiveInputException,
)
from phabfive.settings import deprecation_message, modernize, warn_legacy, warn_once

# 3rd party imports
import anyconfig
import appdirs
from phabricator import Phabricator


log = logging.getLogger(__name__)


class Phabfive:
    # Output formatting options. The defaults suit a program: plain strings
    # rather than rich hyperlinks in returned records, and no rich-only line
    # width limit. The command sets its own through set_output_options.
    _ascii_when = "auto"
    _hyperlink_when = "never"
    _output_format = None
    _fallback_format = "yaml"  # Format used when stdout is not a TTY
    # Maximum line width for rich format (to prevent YAML breaking)
    MAX_LINE_WIDTH = 4096
    # Where instance configuration looked up once - the task statuses - is
    # remembered between runs: an object with get(namespace) and
    # set(namespace, value). None suits a program, which asks the server
    # every time rather than writing to the user's cache directory; the
    # command gives its apps one, see phabfive.cli.lookups.
    lookup_store = None

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

    @classmethod
    def set_fallback_format(cls, fallback):
        """Set the format used when stdout is not a terminal (PHORGE_FALLBACK).

        Aliases are resolved here, so the rest of the program only ever sees
        a real OutputFormat value, exactly as --format=ndjson is resolved.
        """
        cls._fallback_format = FORMAT_ALIASES.get(fallback, fallback)

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
            str: "rich" if stdout is a terminal, PHORGE_FALLBACK if piped/redirected
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
            from rich.text import Text

            t = Text(text)
            t.stylize(f"link {url}")
            return t
        return url if show_url else text

    def get_console(self):
        """Get a Rich Console instance for output."""
        from rich.console import Console

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
            The instance, as ``PHORGE_HOST`` names it: ``phorge.example.com``
            or ``https://phorge.example.com``. A pasted ``/api/`` endpoint is
            forgiven.
        token : str, optional
            A Conduit API token.
        config : dict, optional
            Any of the CONFIGURABLES keys, e.g. ``{"PHORGE_SPACE": "S2"}``. A
            deprecated ``PHAB_*`` key is renamed to its successor, with a
            DeprecationWarning.
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
            self._explicit_host = bool(self.conf.get("PHORGE_HOST"))

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
        # A configuration built by a program, rather than discovered, carries
        # only the keys it cares about: validators constrain the values that are
        # there, while REQUIRED above is what constrains presence.
        for validator_key in VALIDATORS.keys():
            if validator_key not in self.conf:
                continue
            if not re.match(VALIDATORS[validator_key], self.conf[validator_key]):
                error = f"{validator_key} is malformed"
                example = VALIDATION_HINTS.get(validator_key)

                if example:
                    error += ", " + example

                raise PhabfiveConfigException(error)

        # PHORGE_PACE is only read by a batch of writes, but checked here with
        # the retry settings, so that a bad value fails before any work
        # rather than after a batch edit has been planned and confirmed.
        Pacer.from_conf(self.conf)
        self.phab = Conduit(self._client_factory(), RetryPolicy.from_conf(self.conf))

        # Every link phabfive hands back is rooted here: the host, or the
        # address PHORGE_WEB_HOST says a reader reaches the instance at when
        # this process reaches it elsewhere - _resolve_hosts has settled that
        self.url = self.conf["PHORGE_HOST"]

        if verify:
            self.verify_connection()

    @classmethod
    def _explicit_config(cls, url, token, config):
        """Build a configuration from arguments alone, discovering nothing."""
        config, legacy = modernize(dict(config or {}))
        if legacy:
            # A program's deprecation, so the Python one: it reaches the
            # program's own warning filters, and names the line to change
            warnings.warn(
                deprecation_message(legacy, "config"),
                DeprecationWarning,
                stacklevel=3,
            )

        unknown = sorted(set(config) - set(CONFIGURABLES))
        if unknown:
            raise PhabfiveConfigException(
                f"Unknown configuration key(s): {', '.join(unknown)}. "
                f"Known keys: {', '.join(CONFIGURABLES)}"
            )

        conf = copy.deepcopy(DEFAULTS)
        conf.update(config)
        if url is not None:
            conf["PHORGE_HOST"] = url
        if token is not None:
            conf["PHORGE_TOKEN"] = token

        return cls._resolve_hosts(conf, supersedes_explicit=url is not None)

    @classmethod
    def _from_parent(cls, parent):
        """Make an instance that shares `parent`'s configuration and client.

        For an app that needs another app's methods - Diffusion reading
        credentials through Passphrase, Edit editing through Maniphest.
        Constructing the sibling would read the configuration again, and
        build and connect a second client to the same host.
        """
        child = cls.__new__(cls)
        for name in ("conf", "url", "phab", "_explicit_host", "lookup_store"):
            if hasattr(parent, name):
                setattr(child, name, getattr(parent, name))
        return child

    def supports_constraint(self, method, key, value):
        """Whether the ``*.search`` method `method` takes the constraint `key`.

        Neither Phorge nor Phabricator reports a version, so this sends a
        one-row search constrained on `key` set to `value`, once per client;
        see `phabfive.capabilities`.
        """
        from phabfive.capabilities import supports_constraint

        return supports_constraint(self.phab, method, key, value)

    def supports_method(self, method):
        """Whether the server has the Conduit method `method`, asked once."""
        from phabfive.capabilities import supports_method

        return supports_method(self.phab, method)

    def _client_factory(self):
        """Return the recipe for this instance's Conduit client.

        `Phabricator` is looked up in this module when the client is built,
        not when the recipe is made, so a test that patches
        `phabfive.core.Phabricator` still reaches every instance.
        """
        host = self.conf.get("PHORGE_API_HOST") or self._normalize_url(
            self.conf.get("PHORGE_HOST")
        )
        token = self.conf.get("PHORGE_TOKEN")

        def build():
            return Phabricator(host=host, token=token)

        return build

    def verify_connection(self):
        """Check the configured host answers, and accepts the token.

        Raises
        ------
        PhabfiveRemoteException
            PhabfiveAPIException for a token the host refuses,
            PhabfiveConnectionException for a host that cannot be reached.
        """
        self.phab.user.whoami()

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
            The current configuration. A token authenticates against the
            endpoint, so ~/.arcrc keys it by the endpoint: the host looked up
            is PHORGE_API_HOST when it is set, else PHORGE_HOST, when either
            is.

        Returns
        -------
        dict
            Configuration dict with PHORGE_HOST and/or PHORGE_TOKEN if found

        Raises
        ------
        PhabfiveConfigException
            If .arcrc has insecure permissions or multiple hosts without
            PHORGE_HOST
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
        current_url = current_conf.get("PHORGE_API_HOST") or current_conf.get(
            "PHORGE_HOST", ""
        )

        if current_url:
            # A host is already set, try to find matching token in .arcrc
            normalized_current = cls._normalize_url(current_url)

            for host_uri, host_data in hosts.items():
                normalized_host = cls._normalize_url(host_uri)
                if normalized_current == normalized_host:
                    token = host_data.get("token")
                    if token:
                        log.debug(f"Found matching token in .arcrc for {host_uri}")
                        result["PHORGE_TOKEN"] = token
                    break
        else:
            # No host set, use .arcrc hosts
            if len(hosts) == 1:
                # Single host - use both URL and token
                host_uri, host_data = next(iter(hosts.items()))
                token = host_data.get("token")

                log.debug(f"Using single host from .arcrc: {host_uri}")
                result["PHORGE_HOST"] = host_uri
                if token:
                    result["PHORGE_TOKEN"] = token
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
                            result["PHORGE_HOST"] = host_uri
                            if token:
                                result["PHORGE_TOKEN"] = token
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
                            f"Multiple hosts found in ~/.arcrc but PHORGE_HOST is not configured. "
                            f"Please set PHORGE_HOST to specify which host to use:\n  - {host_list}\n\n"
                            f"Example: export PHORGE_HOST=phorge.example.com"
                        )
                    result["PHORGE_HOST"] = selected
                    token = hosts[selected].get("token")
                    if token:
                        result["PHORGE_TOKEN"] = token

        return result

    @classmethod
    def _load_arcconfig(cls):
        """
        Load PHORGE_HOST and PHORGE_WORKBOARD from .arcconfig in the git root.

        Walks up from the current working directory looking for `.git`, then
        reads .arcconfig in that directory. `phabricator.uri` is Arcanist's
        key, normalized to the instance's root address. The `phabfive` object
        is phabfive's own, read by `_read_phabfive_block`; Arcanist ignores a
        key it does not know without a word.

        `.git` is a directory in a clone, and a file in a linked worktree or
        a submodule, where it points at the real one. Either marks the root
        that holds the checkout's .arcconfig.

        Returns
        -------
        dict
            Configuration dict with PHORGE_HOST and/or PHORGE_WORKBOARD if
            found, empty dict otherwise
        """
        # Walk up from cwd looking for .git
        current = os.getcwd()
        git_root = None

        while True:
            if os.path.exists(os.path.join(current, ".git")):
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

        if not isinstance(arcconfig_data, dict):
            log.warning(f"Ignoring {arcconfig_path}: not a JSON object")
            return {}

        result = {}

        uri = arcconfig_data.get("phabricator.uri")
        if uri:
            result["PHORGE_HOST"] = cls._host_root(uri)
            log.debug(f"Using PHORGE_HOST from .arcconfig: {result['PHORGE_HOST']}")
        else:
            log.debug("No phabricator.uri found in .arcconfig")

        if "phabfive" in arcconfig_data:
            result.update(
                cls._read_phabfive_block(arcconfig_data["phabfive"], arcconfig_path)
            )

        return result

    @staticmethod
    def _read_phabfive_block(block, path):
        """Read the `phabfive` object of an .arcconfig.

        One object rather than dotted keys beside Arcanist's, so that
        everything in it is phabfive's and can be checked strictly: a key
        phabfive does not know is a typo, and is said to be one, where a
        dotted key could as well have been another tool's.

            "phabfive": {
              "tags": {
                "workboard": "dynatron",
                "hints": {"security-risk": "what makes one here", "textarion": null}
              }
            }

        `tags.workboard` is the project a repository's tasks live on, as
        `--tag` takes it, and becomes PHORGE_WORKBOARD. `tags.hints` names
        other tags worth considering, each with an optional note; it is
        checked here and not yet used. Whatever is malformed is warned about
        and left out, never fatal, so a bad .arcconfig cannot stop a command
        that does not need it.

        Returns
        -------
        dict
            {"PHORGE_WORKBOARD": ...} when a workboard is named, else {}
        """

        def warn(where, expected):
            warn_once(path, f"Ignoring {where} in {path}: expected {expected}")

        def unknown(where, mapping, known):
            for key in sorted(set(mapping) - known):
                warn_once(
                    path,
                    f"Ignoring unknown key {where}.{key} in {path}; "
                    f"known keys: {', '.join(sorted(known))}",
                )

        if not isinstance(block, dict):
            warn("phabfive", "an object")
            return {}
        unknown("phabfive", block, {"tags"})

        tags = block.get("tags")
        if tags is None:
            return {}
        if not isinstance(tags, dict):
            warn("phabfive.tags", "an object")
            return {}
        unknown("phabfive.tags", tags, {"workboard", "hints"})

        hints = tags.get("hints")
        if hints is not None:
            if not isinstance(hints, dict):
                warn("phabfive.tags.hints", "an object of tag: note")
            else:
                for tag, note in hints.items():
                    if note is not None and not isinstance(note, str):
                        warn(
                            f"phabfive.tags.hints.{tag}", "a note as a string, or null"
                        )

        result = {}
        workboard = tags.get("workboard")
        if isinstance(workboard, str) and workboard.strip():
            result["PHORGE_WORKBOARD"] = workboard.strip()
            log.debug(f"Using PHORGE_WORKBOARD from .arcconfig: {workboard.strip()}")
        elif workboard is not None:
            warn(
                "phabfive.tags.workboard",
                "a project name, #hashtag, ID or PHID as a string",
            )
        return result

    @staticmethod
    def _site_config_base():
        """Where the system-wide configuration lives, less its suffix.

        `/etc/phabfive`, so `/etc/phabfive.yaml` and `/etc/phabfive.d/`. This
        used to be reached by setting os.environ["XDG_CONFIG_DIRS"] = "/etc"
        before asking appdirs, which rewrote the variable for the whole
        process - and for every program that imported phabfive, and every
        child it started. appdirs only reads XDG on Linux and the BSDs, so
        elsewhere its answer is still the one to use.
        """
        if sys.platform == "darwin" or sys.platform.startswith("win"):
            return appdirs.site_config_dir("phabfive")
        return "/etc/phabfive"

    def load_config(self, select_host=None):
        """
        Load configuration and remember whether the host was chosen explicitly.

        See read_config for the search order.
        """
        conf, explicit_host = type(self).read_config(select_host=select_host)
        self._explicit_host = explicit_host
        return conf

    @classmethod
    def _settings_from(cls, mapping, where):
        """The settings in `mapping`, a loaded yaml file or the environment.

        Keys that are not settings are dropped, and a deprecated ``PHAB_*``
        key is renamed to its ``PHORGE_*`` successor with a warning naming
        `where` it was found.
        """
        renamed, legacy = modernize(dict(mapping))
        if legacy:
            warn_legacy(legacy, where)
        return {key: value for key, value in renamed.items() if key in CONFIGURABLES}

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
          6. `.arcconfig` in git root (PHORGE_HOST, PHORGE_WORKBOARD)
          7. `~/.arcrc` (Arcanist configuration)
          8. environment variables

        A classmethod because the cache needs the endpoint to key its entries
        before it knows whether it will ask the server anything, and a
        Phabfive() validates a whole configuration it does not need.

        Parameters
        ----------
        select_host : callable, optional
            Called with the list of hosts when ~/.arcrc holds several, no
            PHORGE_HOST is configured and ~/.arcrc names no default. Returns
            the chosen host.

        Returns
        -------
        tuple
            (conf, explicit_host), where explicit_host is whether the host
            came from anything other than ~/.arcrc
        """
        environ = cls._settings_from(os.environ, "the environment")

        log.debug("Loading configuration defaults")
        conf = copy.deepcopy(DEFAULTS)

        site_conf_file = f"{cls._site_config_base()}.yaml"
        log.debug(f"Loading configuration file: {site_conf_file}")
        anyconfig.merge(
            conf,
            cls._settings_from(
                anyconfig.load(site_conf_file, ac_ignore_missing=True),
                site_conf_file,
            ),
        )

        site_conf_dir = os.path.join(f"{cls._site_config_base()}.d", "*.yaml")
        log.debug(f"Loading configuration files: {site_conf_dir}")
        anyconfig.merge(
            conf,
            cls._settings_from(anyconfig.multi_load(site_conf_dir), site_conf_dir),
        )

        # Track config state before user yaml files to detect deprecated keys
        pre_user_host = conf.get("PHORGE_HOST", "")
        pre_user_token = conf.get("PHORGE_TOKEN", "")

        user_conf_file = os.path.join(f"{appdirs.user_config_dir('phabfive')}.yaml")
        log.debug(f"Loading configuration file: {user_conf_file}")
        cls._check_secure_permissions(user_conf_file)
        anyconfig.merge(
            conf,
            cls._settings_from(
                anyconfig.load(user_conf_file, ac_ignore_missing=True),
                user_conf_file,
            ),
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
            cls._settings_from(anyconfig.multi_load(user_conf_dir), user_conf_dir),
        )

        # Warn if the host or token were set from user yaml config files
        deprecated_keys = []
        if conf.get("PHORGE_HOST", "") != pre_user_host and conf.get("PHORGE_HOST", ""):
            deprecated_keys.append("PHORGE_HOST")
        if conf.get("PHORGE_TOKEN", "") != pre_user_token and conf.get(
            "PHORGE_TOKEN", ""
        ):
            deprecated_keys.append("PHORGE_TOKEN")
        if deprecated_keys:
            keys_str = "/".join(deprecated_keys)
            log.warning(
                f"~/.config/phabfive.yaml contains {keys_str} which is deprecated. "
                "Migrate credentials to ~/.arcrc using: phabfive user setup"
            )

        # Load from .arcconfig in git repository root
        arcconfig_conf = cls._load_arcconfig()
        if arcconfig_conf:
            log.debug("Merging configuration from .arcconfig")
            anyconfig.merge(conf, arcconfig_conf)

        # Remember whether a host was chosen by anything other than ~/.arcrc.
        # Everything merged so far (site and user yaml, .arcconfig) plus the
        # environment means the user pointed phabfive at a specific host; a
        # host that only comes out of ~/.arcrc does not.
        explicit_host = bool(conf.get("PHORGE_HOST")) or bool(
            environ.get("PHORGE_HOST")
        )

        # Load from Arcanist .arcrc file (supports single or multiple hosts).
        # Include the host from the environment so .arcrc can match the right
        # one even though env vars are formally merged later. A token
        # authenticates against the endpoint, so ~/.arcrc keys it by the
        # endpoint: a split configuration has to match on PHORGE_API_HOST or
        # it looks up a host that was never written; _load_arcrc prefers it
        arcrc_lookup_conf = dict(conf)
        for key in ("PHORGE_HOST", "PHORGE_API_HOST"):
            if environ.get(key):
                arcrc_lookup_conf[key] = environ[key]
        arcrc_conf = cls._load_arcrc(arcrc_lookup_conf, select_host=select_host)
        if arcrc_conf:
            log.debug("Merging configuration from ~/.arcrc")
            anyconfig.merge(conf, arcrc_conf)

        log.debug("Loading configuration from environment variables")
        anyconfig.merge(conf, environ)

        # Last, so that the two shapes are judged on the merged configuration.
        # Half a pair in one layer and half in another is a valid split, and a
        # per-layer check would reject it.
        conf = cls._resolve_hosts(conf, supersedes_explicit=explicit_host)

        if conf.get("PHORGE_API_HOST"):
            explicit_host = True

        return conf, explicit_host

    def has_explicit_host(self):
        """
        Whether PHORGE_HOST was configured outside of ~/.arcrc.

        True when the host came from an environment variable, `.arcconfig`,
        or a phabfive yaml config, i.e. the user pointed phabfive at one
        specific host. False when the host was only discovered by reading
        ~/.arcrc, or when no host is configured at all.

        Returns
        -------
        bool
        """
        return getattr(self, "_explicit_host", False)

    def has_explicit_phab_url(self):
        """Deprecated spelling of :meth:`has_explicit_host`."""
        return self.has_explicit_host()

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
    def _host_root(cls, host):
        """
        Normalize an instance's address to its root, with no trailing slash.

        A bare host is reached over https, and a pasted endpoint is forgiven.

        - phorge.example.com -> https://phorge.example.com
        - https://phorge.example.com/ -> https://phorge.example.com
        - https://phorge.example.com/api/ -> https://phorge.example.com
        - http://phorge.localhost:8080 -> http://phorge.localhost:8080
        """
        host = host.strip()
        if host and "://" not in host:
            host = f"https://{host}"
        host = host.rstrip("/")
        if host.endswith("/api"):
            host = host[: -len("/api")]
        return host.rstrip("/")

    @classmethod
    def _web_root(cls, host):
        """
        Normalize an address a reader opens: a scheme when missing, no slash.

        Unlike `_host_root` this keeps a path, so that an endpoint pasted
        into PHORGE_WEB_HOST is refused by its validator rather than turned
        into links that look right and open nothing.
        """
        host = host.strip()
        if host and "://" not in host:
            host = f"https://{host}"
        return host.rstrip("/")

    @classmethod
    def _normalize_url(cls, url):
        """
        Normalize an instance's address to its Conduit endpoint.

        Handles various input formats:
        - phorge.example.com -> https://phorge.example.com/api/
        - https://phorge.example.com -> https://phorge.example.com/api/
        - https://phorge.example.com/ -> https://phorge.example.com/api/
        - https://phorge.example.com/api -> https://phorge.example.com/api/
        - https://phorge.example.com/api/ -> https://phorge.example.com/api/
        """
        return f"{cls._host_root(url)}/api/"

    @classmethod
    def _resolve_hosts(cls, conf, supersedes_explicit=False):
        """
        Settle PHORGE_HOST, PHORGE_API_HOST and PHORGE_WEB_HOST into a configuration.

        PHORGE_HOST is one address doing two jobs: the Conduit endpoint
        phabfive calls, at PHORGE_HOST/api/, and the root of every link it
        hands back. Those are the same address almost everywhere, which is
        why one setting has held up; they come apart behind an ingress or
        split-horizon DNS, where a caller reaching a Service name still has
        to emit links a reader can open.

        So a configuration has exactly two valid shapes: PHORGE_HOST on its
        own, or PHORGE_API_HOST and PHORGE_WEB_HOST together. Half a pair is
        an error rather than a guess, because the missing half is precisely
        what cannot be derived -- stripping /api/ off an endpoint is the
        assumption that fails in the only case this exists for.

        The pair resolves *into* PHORGE_HOST, which then holds the address a
        reader opens, so every link is rooted at one setting downstream. The
        endpoint stays in PHORGE_API_HOST, and everything keyed by instance
        -- the token in ~/.arcrc, the cache directory -- keys on it when it
        is set and on PHORGE_HOST/api/ otherwise: the address it
        authenticates against, which is what those keys mean by an instance.

        Parameters
        ----------
        conf : dict
            A merged configuration.
        supersedes_explicit : bool
            Whether a PHORGE_HOST in `conf` was chosen deliberately rather
            than read out of ~/.arcrc. Only then is being superseded worth a
            word: ~/.arcrc carries a host for every stored token, so warning
            about that one would fire on every ordinary run.

        Returns
        -------
        dict
            `conf`, with every address normalized and PHORGE_HOST holding
            the address a reader opens.

        Raises
        ------
        PhabfiveConfigException
            When one half of the pair is configured without the other.
        """
        host = (conf.get("PHORGE_HOST") or "").strip()
        api = (conf.get("PHORGE_API_HOST") or "").strip()
        web = (conf.get("PHORGE_WEB_HOST") or "").strip()

        if host:
            conf["PHORGE_HOST"] = cls._host_root(host)

        if not api and not web:
            return conf

        if not api or not web:
            given, missing = (
                ("PHORGE_WEB_HOST", "PHORGE_API_HOST")
                if web
                else ("PHORGE_API_HOST", "PHORGE_WEB_HOST")
            )
            raise PhabfiveConfigException(
                f"{given} is configured without {missing}. Neither address can "
                f"be derived from the other: configure both, or configure "
                f"PHORGE_HOST alone when the endpoint and the address a reader "
                f"opens are the same"
            )

        conf["PHORGE_API_HOST"] = cls._normalize_url(api)
        conf["PHORGE_WEB_HOST"] = cls._web_root(web)

        previous = conf.get("PHORGE_HOST") or ""
        if supersedes_explicit and previous and previous != conf["PHORGE_WEB_HOST"]:
            log.warning(
                f"PHORGE_HOST ({previous}) is ignored because PHORGE_API_HOST and "
                f"PHORGE_WEB_HOST are configured; the endpoint is "
                f"{conf['PHORGE_API_HOST']} and links open at "
                f"{conf['PHORGE_WEB_HOST']}"
            )
        conf["PHORGE_HOST"] = conf["PHORGE_WEB_HOST"]

        return conf

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

        raise PhabfiveInputException(f"No valid monogram found in: {text}")

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
            raise PhabfiveInputException("No valid object IDs provided")

        results = []
        seen_types = set()

        for id_str in ids:
            obj_type, obj_id = self.parse_monogram(id_str)
            results.append((obj_type, obj_id))
            seen_types.add(obj_type)

        # Check for mixed types
        if len(seen_types) > 1:
            raise PhabfiveInputException(
                f"Cannot mix object types in a single edit command: {seen_types}"
            )

        return results


__all__ = ["Phabfive"]
