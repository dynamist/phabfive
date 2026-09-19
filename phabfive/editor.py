# -*- coding: utf-8 -*-
"""External editor support for phabfive."""

import difflib
import os
import subprocess
import sys
import tempfile


def get_editor():
    """Get the user's preferred editor."""
    return os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"


def edit_text(initial_text="", prefix="", suffix=".remarkup"):
    """Open external editor and return edited text.

    Args:
        initial_text: Text to pre-populate the editor with
        prefix: File prefix (hints at what field is being edited)
        suffix: File suffix (helps editors with syntax highlighting)

    Returns:
        str: The edited text, or None if:
            - User saves empty file (cancellation)
            - Content unchanged from initial_text
            - Editor returns non-zero exit code
    """
    with tempfile.NamedTemporaryFile(
        mode="w", prefix=prefix, suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        f.write(initial_text)
        temp_path = f.name

    try:
        editor = get_editor()
        result = subprocess.run([editor, temp_path])

        if result.returncode != 0:
            return None

        with open(temp_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Normalize and compare
        content = content.rstrip()
        if not content or content == initial_text.rstrip():
            return None

        return content
    finally:
        os.unlink(temp_path)


def show_diff(old_text, new_text, filename="description"):
    """Display unified diff between old and new text.

    Uses colors if stdout is a TTY.

    Args:
        old_text: Original text
        new_text: New text
        filename: Name to show in diff header
    """
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)

    # Ensure trailing newline for proper diff
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )

    if not diff:
        return  # No changes

    use_color = sys.stdout.isatty()

    for line in diff:
        if use_color:
            if line.startswith("+") and not line.startswith("+++"):
                print(f"\033[32m{line}\033[0m", end="")  # Green
            elif line.startswith("-") and not line.startswith("---"):
                print(f"\033[31m{line}\033[0m", end="")  # Red
            elif line.startswith("@@"):
                print(f"\033[36m{line}\033[0m", end="")  # Cyan
            else:
                print(line, end="")
        else:
            print(line, end="")


def render_changes(monogram, changes, header=None):
    """Print one object's pending changes.

    Args:
        monogram (str): Object monogram (e.g., "T123")
        changes (list): Dicts with 'field', 'old' and 'new' keys
        header (str): Line to print first; defaults to "<monogram>:"
    """
    print(header if header is not None else f"{monogram}:")

    for change in changes:
        field = change["field"]
        old = change["old"]
        new = change["new"]

        if field == "Title":
            # A title is free text, so a diff reads better than "old → new"
            print()
            show_diff(old, new, filename="title")
        elif old is None:
            print(f"  {field}: {new}")
        else:
            print(f"  {field}: {old} → {new}")


def resolve_assume_yes(yes, force, interactive=False):
    """Combine --yes with the deprecated --force alias, rejecting --yes --interactive.

    Args:
        yes (bool): Value of --yes
        force (bool): Value of the hidden --force alias
        interactive (bool): Value of --interactive

    Returns:
        bool: Whether confirmation prompts should be answered automatically

    Raises:
        ValueError: If the caller asked both to skip and to make every prompt
    """
    if force:
        sys.stderr.write("WARNING: --force is deprecated, use --yes instead.\n")

    assume_yes = yes or force
    if assume_yes and interactive:
        raise ValueError("--yes and --interactive are mutually exclusive")

    return assume_yes


def open_tty():
    """Open the controlling terminal for reading and writing.

    Lets a review prompt work when stdin is a pipe, the way git does. Returns
    None when there is no controlling terminal, which is the case in CI and in
    most agent subprocesses - the caller must then not prompt.

    Returns:
        io.TextIOWrapper or None
    """
    try:
        return open("/dev/tty", "r+")
    except OSError:
        return None


REVIEW_KEYS = {
    "y": "apply this change",
    "n": "skip it",
    "a": "apply this and all remaining",
    "q": "quit, applying nothing further",
}


def prompt_each(monogram, stream=None):
    """Ask what to do with one object's changes.

    Reads a line rather than a raw keypress, so it needs no termios and works
    when stdin is a pipe.

    Args:
        monogram (str): Object monogram (e.g., "T123")
        stream: Terminal to read from and write to; defaults to stdin/stdout

    Returns:
        str: One of "y", "n", "a", "q". EOF is treated as "q".
    """
    prompt = f"Apply {monogram}? [y,n,a,q,?] "

    while True:
        if stream is None:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            answer = sys.stdin.readline()
        else:
            stream.write(prompt)
            stream.flush()
            answer = stream.readline()

        if not answer:
            # EOF - stop rather than guess at consent
            print()
            return "q"

        key = answer.strip().lower()[:1]
        if key in REVIEW_KEYS:
            return key

        for review_key, meaning in REVIEW_KEYS.items():
            line = f"{review_key} - {meaning}\n"
            if stream is None:
                sys.stdout.write(line)
            else:
                stream.write(line)


def confirm_apply(assume_yes, prompt="Apply changes?"):
    """Ask for confirmation, or take it from assume_yes.

    This only answers a prompt. It never decides whether a write happens - that
    stays with --dry-run, which short-circuits before the API call.

    Args:
        assume_yes (bool): Skip the prompt and confirm
        prompt (str): Question to ask when there is a terminal

    Returns:
        tuple: (confirmed: bool, return_code: int or None)
               If confirmed is False, return_code indicates exit code
    """
    import typer

    if assume_yes:
        return (True, None)

    # The diff went to stdout; flush it so a merged capture keeps the order.
    sys.stdout.flush()

    if not sys.stdin.isatty():
        sys.stderr.write("Error: --yes required for non-interactive mode\n")
        return (False, 1)

    try:
        if typer.confirm(prompt):
            return (True, None)
        else:
            return (False, 0)
    except typer.Abort:
        print("Cancelled")
        return (False, 0)


def confirm_text_change(
    old_text, new_text, assume_yes, filename="description", dry_run=False
):
    """Show diff and get confirmation for text change.

    Args:
        old_text (str): Current text content
        new_text (str): New text content
        assume_yes (bool): Skip confirmation prompt
        filename (str): Name to show in diff header (e.g., "title", "description")
        dry_run (bool): Previewing only, so there is nothing to confirm

    Returns:
        tuple: (confirmed: bool, return_code: int or None)
               If confirmed is False, return_code indicates exit code
    """
    print()
    show_diff(old_text, new_text, filename=filename)
    print()

    return confirm_apply(assume_yes or dry_run)
