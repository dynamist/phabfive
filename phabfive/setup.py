# -*- coding: utf-8 -*-
"""Interactive first-run configuration setup for phabfive."""

import logging
import os
import re
import sys

from phabricator import Phabricator, APIError
from rich.console import Console
from rich.prompt import Prompt, Confirm
from ruamel.yaml import YAML

from phabfive.constants import VALIDATORS


def _read_password_with_dots(prompt: str = "") -> str:
    """Read password input showing dots for each character typed.

    Args:
        prompt: The prompt to display before input

    Returns:
        The password string entered by the user
    """
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()

    password = []

    if os.name == "nt":
        # Windows implementation using msvcrt
        import msvcrt

        while True:
            char = msvcrt.getwch()
            if char in ("\r", "\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                break
            elif char == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
            elif char == "\x08":  # Backspace
                if password:
                    password.pop()
                    # Erase the dot: move back, write space, move back
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            else:
                password.append(char)
                sys.stdout.write("\u2022")  # bullet dot
                sys.stdout.flush()
    else:
        # Unix implementation using termios
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                char = sys.stdin.read(1)
                if char in ("\r", "\n"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    break
                elif char == "\x03":  # Ctrl+C
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    raise KeyboardInterrupt
                elif char == "\x7f" or char == "\x08":  # Backspace/Delete
                    if password:
                        password.pop()
                        # Erase the dot: move back, write space, move back
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                elif char >= " ":  # Printable characters only
                    password.append(char)
                    sys.stdout.write("\u2022")  # bullet dot
                    sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return "".join(password)


log = logging.getLogger(__name__)


class SetupWizard:
    """Interactive setup wizard for phabfive configuration."""

    CONFIG_PATH = os.path.expanduser("~/.config/phabfive.yaml")

    def __init__(self):
        self.console = Console()
        self.phab_url = ""
        self.phab_token = ""

    def run(self) -> bool:
        """Run the interactive setup wizard.

        Returns:
            bool: True if setup completed successfully, False otherwise
        """
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

        while True:
            token = _read_password_with_dots("Enter your API token: ")

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
        """Save configuration to file with secure permissions."""
        try:
            # Ensure ~/.config directory exists
            config_dir = os.path.dirname(self.CONFIG_PATH)
            os.makedirs(config_dir, mode=0o700, exist_ok=True)

            # Load existing config or create new
            yaml = YAML()
            yaml.default_flow_style = False

            if os.path.exists(self.CONFIG_PATH):
                with open(self.CONFIG_PATH, "r") as f:
                    config = yaml.load(f) or {}
            else:
                config = {}

            # Update with new values
            config["PHAB_URL"] = self.phab_url
            config["PHAB_TOKEN"] = self.phab_token

            # Write config file
            with open(self.CONFIG_PATH, "w") as f:
                yaml.dump(config, f)

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


def offer_setup_on_error(error_message: str) -> bool:
    """Offer to run setup when configuration error occurs.

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

    if Confirm.ask("Would you like to run interactive setup now?", default=True):
        wizard = SetupWizard()
        return wizard.run()

    return False
