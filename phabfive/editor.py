# -*- coding: utf-8 -*-
"""External editor support for phabfive."""

import os
import subprocess
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
