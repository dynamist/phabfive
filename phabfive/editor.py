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


def edit_text(initial_text="", suffix=".remarkup"):
    """Open external editor and return edited text.

    Args:
        initial_text: Text to pre-populate the editor with
        suffix: File suffix (helps editors with syntax highlighting)

    Returns:
        str: The edited text, or None if:
            - User saves empty file (cancellation)
            - Content unchanged from initial_text
            - Editor returns non-zero exit code
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
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
