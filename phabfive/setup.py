# -*- coding: utf-8 -*-
"""Interactive first-run configuration setup for phabfive."""

import json
import logging
import os
import re
import sys

from phabricator import Phabricator, APIError
from rich.console import Console
from rich.prompt import Prompt, Confirm

from phabfive.constants import VALIDATORS


log = logging.getLogger(__name__)


class SetupWizard:
    """Interactive setup wizard for phabfive configuration."""

    CONFIG_PATH = os.path.expanduser("~/.arcrc")

    def __init__(self):
        self.console = Console()
        self.phab_url = ""
        self.phab_token = ""

    def _check_existing_config(self) -> bool:
        """Check if there's an existing working configuration.

        If a working config exists, warn the user and ask for confirmation.

        Returns:
            bool: True to proceed with setup, False to abort
        """
        try:
            from phabfive.core import Phabfive

            # Try to create a Phabfive instance - this validates config and connection
            phabfive = Phabfive()
            whoami = phabfive.phab.user.whoami()
            username = whoami.get("userName", "unknown")

            self.console.print(
                "\n[yellow]Warning:[/yellow] A working configuration already exists."
            )
            self.console.print(
                f"Currently connected to [bold]{phabfive.conf['PHAB_URL']}[/bold] "
                f"as [bold]{username}[/bold].\n"
            )

            if not Confirm.ask("Do you want to reconfigure phabfive?", default=False):
                self.console.print("Setup cancelled.\n")
                return False

            return True

        except Exception:
            # No valid config or connection failed - proceed with setup
            return True

    def run(self) -> bool:
        """Run the interactive setup wizard.

        Returns:
            bool: True if setup completed successfully, False otherwise
        """
        # Check for existing working configuration
        if not self._check_existing_config():
            return False

        self._print_header()

        # Step 1: Get URL
        if not self._prompt_url():
            return False

        # Step 2: Get Token
        if not self._prompt_token():
            return False

        # Step 3: Verify connection
        if not self._verify_connection():
            return False

        # Step 4: Save configuration
        if not self._save_config():
            return False

        self._print_success()
        return True

    def _print_header(self):
        """Print the setup wizard header."""
        self.console.print("\n[bold]Phabfive Setup[/bold]")
        self.console.print("=" * 40)
        self.console.print(
            "\nThis wizard will configure phabfive to connect to your "
            "Phabricator/Phorge instance.\n"
        )

    def _prompt_url(self) -> bool:
        """Prompt for and validate the Phabricator URL."""
        self.console.print("[bold][1/3] Phabricator URL[/bold]")

        while True:
            url = Prompt.ask(
                "Enter your Phabricator URL (e.g., https://phorge.example.com)"
            )

            if not url:
                self.console.print("[red]URL cannot be empty[/red]")
                continue

            # Normalize URL
            normalized = self._normalize_url(url)

            # Validate against pattern
            if not re.match(VALIDATORS["PHAB_URL"], normalized):
                self.console.print(
                    "[red]Invalid URL format. "
                    "Expected format: https://phorge.example.com[/red]"
                )
                continue

            self.phab_url = normalized
            self.console.print(f"[green]> Using API endpoint: {normalized}[/green]\n")
            return True

    def _prompt_token(self) -> bool:
        """Prompt for and validate the API token."""
        self.console.print("[bold][2/3] API Token[/bold]")

        # Extract base URL for settings link
        from urllib.parse import urlparse

        parsed = urlparse(self.phab_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        self.console.print("To create an API token:")
        self.console.print(
            f"  1. Go to {base_url}/settings/user/YOUR_USERNAME/page/apitokens/"
        )
        self.console.print('  2. Click "Generate API Token"')
        self.console.print("  3. Copy the token (starts with 'cli-')\n")

        from InquirerPy import inquirer as inq

        while True:
            token = inq.secret(message="Enter your API token:").execute()

            if not token:
                self.console.print("[red]Token cannot be empty[/red]")
                continue

            # Validate token format
            if not re.match(VALIDATORS["PHAB_TOKEN"], token):
                self.console.print(
                    "[red]Invalid token format. Token should be 32 alphanumeric "
                    "characters (typically starts with 'cli-')[/red]"
                )
                continue

            self.phab_token = token
            self.console.print()
            return True

    def _verify_connection(self) -> bool:
        """Verify the connection to Phabricator."""
        self.console.print("[bold][3/3] Verifying connection...[/bold]")

        try:
            phab = Phabricator(host=self.phab_url, token=self.phab_token)
            phab.update_interfaces()
            whoami = phab.user.whoami()

            username = whoami.get("userName", "unknown")
            realname = whoami.get("realName", "")

            if realname:
                self.console.print(
                    f"[green]> Connected successfully as: "
                    f"{username} ({realname})[/green]\n"
                )
            else:
                self.console.print(
                    f"[green]> Connected successfully as: {username}[/green]\n"
                )

            return True

        except APIError as e:
            self.console.print(f"[red]> Connection failed: {e}[/red]")
            self.console.print("[red]Please check your URL and token.[/red]\n")

            if Confirm.ask("Would you like to try again?", default=True):
                return (
                    self._prompt_url()
                    and self._prompt_token()
                    and self._verify_connection()
                )
            return False

        except Exception as e:
            self.console.print(f"[red]> Connection error: {e}[/red]\n")
            return False

    def _save_config(self) -> bool:
        """Save credentials to ~/.arcrc in Arcanist-compatible JSON format."""
        try:
            # Load existing .arcrc or create new
            if os.path.exists(self.CONFIG_PATH):
                with open(self.CONFIG_PATH, "r") as f:
                    arcrc = json.load(f)
            else:
                arcrc = {}

            # Ensure hosts key exists
            if "hosts" not in arcrc:
                arcrc["hosts"] = {}

            # Add/update the host entry
            arcrc["hosts"][self.phab_url] = {"token": self.phab_token}

            # Write JSON with indentation
            with open(self.CONFIG_PATH, "w") as f:
                json.dump(arcrc, f, indent=2)
                f.write("\n")

            # Set secure permissions (Unix only)
            if os.name != "nt":
                os.chmod(self.CONFIG_PATH, 0o600)

            return True

        except Exception as e:
            self.console.print(f"[red]Failed to save configuration: {e}[/red]")
            return False

    def _print_success(self):
        """Print success message."""
        self.console.print(f"[green]Configuration saved to {self.CONFIG_PATH}[/green]")
        if os.name != "nt":
            self.console.print(
                "[green]File permissions set to 0600 (owner read/write only)[/green]"
            )
        self.console.print("\n[bold]Example commands:[/bold]")
        self.console.print("  phabfive user whoami")
        self.console.print("  phabfive maniphest search --assigned @me\n")

    def _normalize_url(self, url: str) -> str:
        """Normalize URL to end with /api/."""
        url = url.rstrip("/")
        if not url.endswith("/api"):
            url += "/api"
        url += "/"
        return url


def _find_git_root():
    """Find the git repository root by walking up from cwd.

    Returns:
        str or None: Path to git root, or None if not in a git repo
    """
    current = os.getcwd()
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _setup_arcconfig(console) -> bool:
    """Interactive setup to create .arcconfig in the git repo root.

    Args:
        console: Rich Console instance for output

    Returns:
        bool: True if .arcconfig was created successfully, False otherwise
    """
    git_root = _find_git_root()

    if git_root is None:
        console.print(
            "[yellow]Not inside a git repository. Cannot create .arcconfig.[/yellow]\n"
        )
        console.print("Either:")
        console.print("  1. Run phabfive from inside a git repository")
        console.print("  2. Set PHAB_URL environment variable")
        console.print(
            "  3. Run [bold]phabfive user setup[/bold] to configure ~/.arcrc\n"
        )
        return False

    arcconfig_path = os.path.join(git_root, ".arcconfig")

    if os.path.exists(arcconfig_path):
        console.print(
            f"[yellow].arcconfig already exists at {arcconfig_path}[/yellow]\n"
        )
        try:
            with open(arcconfig_path, "r") as f:
                data = json.load(f)
            uri = data.get("phabricator.uri", "(not set)")
            console.print(f"  Current phabricator.uri: [bold]{uri}[/bold]\n")
        except (json.JSONDecodeError, IOError):
            pass

        if not Confirm.ask("Do you want to overwrite it?", default=False):
            return False

    console.print("[bold]Create .arcconfig[/bold]")
    console.print(f"This will create .arcconfig in: {git_root}\n")

    while True:
        url = Prompt.ask(
            "Enter your Phabricator URL (e.g., https://phorge.example.com)"
        )

        if not url:
            console.print("[red]URL cannot be empty[/red]")
            continue

        # Strip trailing slashes and /api/ suffix for .arcconfig
        # .arcconfig stores the base URL, not the API URL
        url = url.rstrip("/")
        if url.endswith("/api"):
            url = url[:-4].rstrip("/")

        console.print(f"[green]> Using URL: {url}[/green]\n")
        break

    try:
        arcconfig_data = {"phabricator.uri": url + "/"}
        with open(arcconfig_path, "w") as f:
            json.dump(arcconfig_data, f, indent=2)
            f.write("\n")

        console.print(f"[green].arcconfig created at {arcconfig_path}[/green]")
        console.print("[dim]Remember to commit .arcconfig to your repository.[/dim]\n")
        return True

    except Exception as e:
        console.print(f"[red]Failed to create .arcconfig: {e}[/red]")
        return False


def offer_setup_on_error(error_message: str) -> bool:
    """Offer to run setup when configuration error occurs.

    Routes to the appropriate wizard based on what's missing:
    - Missing PHAB_URL: offer to create .arcconfig
    - Missing PHAB_TOKEN or other errors: offer full setup wizard (~/.arcrc)

    Args:
        error_message: The configuration error message to display

    Returns:
        bool: True if user ran setup and it succeeded, False otherwise
    """
    # Only offer interactive setup if stdin is a TTY
    if not sys.stdin.isatty():
        print(f"CRITICAL - {error_message}", file=sys.stderr)
        print("\nTo configure interactively, run: phabfive user setup", file=sys.stderr)
        return False

    console = Console()
    console.print(f"\n[red]ERROR: {error_message}[/red]\n")

    is_url_error = "PHAB_URL" in error_message and "PHAB_TOKEN" not in error_message

    if is_url_error:
        if Confirm.ask(
            "Would you like to create .arcconfig for this repository?",
            default=True,
        ):
            return _setup_arcconfig(console)
    else:
        if Confirm.ask("Would you like to run interactive setup now?", default=True):
            wizard = SetupWizard()
            return wizard.run()

    return False
