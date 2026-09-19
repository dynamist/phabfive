#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Refuse a release whose tag and pyproject.toml disagree about the version.

The tag says which version is being released; pyproject.toml says which version
gets built. They are set by hand -- in the same commit by convention, in
different ones by accident -- and nothing downstream notices when they differ,
because every artifact is named after pyproject.toml while the release is named
after the tag.

v0.10.0-rc.1 was tagged over a pyproject.toml that still read 0.10.0-dev.0, and
the release went out with a wheel, an sdist and six executables all named for
the dev version. Every job reported success. From rc.2 on it was kept in step by
hand, which is a convention, not a check. This is the check. The same slip on a
final tag would publish to PyPI, where a version cannot be replaced.

It runs before anything is built, so the answer arrives in seconds rather than
after six PyInstaller builds and two image builds. The smoke tests check the
same thing again from the other end, against the version a built artifact
reports at runtime, which is what catches a binary built from the wrong
revision.

    python3 scripts/check_version.py v0.11.0-rc.1
    python3 scripts/check_version.py v0.11.0-rc.1 path/to/pyproject.toml

Stdlib only, like scripts/smoke.py: it has to run on a bare CI runner.
"""

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoke import canonical_version  # noqa: E402

# Releases are followed by a bump to a .dev version, so a tag naming one is a
# tag pushed before the release bump -- which is worth refusing even when
# pyproject.toml agrees with it, the one case equality alone lets through.
DEV_MARKERS = ("dev",)


def project_version(pyproject: Path) -> str:
    """The version pyproject.toml declares, and that hatchling will build."""
    if tomllib is None:
        sys.exit("reading pyproject.toml needs tomllib (Python 3.11+)")

    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)

    try:
        return data["project"]["version"]
    except KeyError:
        sys.exit(f"{pyproject} declares no [project] version")


def main(argv: list) -> int:
    if not 2 <= len(argv) <= 3:
        sys.exit("usage: check_version.py TAG [PYPROJECT]")

    tag = argv[1]
    # The workflow only triggers on "v*", but the leading v is not part of the
    # version and canonical_version() does not drop letters.
    tagged = tag[1:] if tag.startswith("v") else tag

    # The second argument exists so the tests can point it at a fixture; a
    # release always checks the pyproject.toml next to this script.
    default = Path(__file__).resolve().parent.parent / "pyproject.toml"
    pyproject = Path(argv[2]) if len(argv) == 3 else default
    declared = project_version(pyproject)

    print(f"tag            {tag}")
    print(f"pyproject.toml {declared}")

    # Compared with the separators dropped, so the tag spelling v0.11.0-rc.1 and
    # the canonical PEP 440 0.11.0rc1 that hatchling builds are one version.
    if canonical_version(tagged) != canonical_version(declared):
        print()
        print(f"tag {tag} would release artifacts named {declared}.")
        print("Set the version in pyproject.toml, commit, and move the tag.")
        return 1

    if any(marker in canonical_version(declared) for marker in DEV_MARKERS):
        print()
        print(f"{declared} is a development version and must not be released.")
        print("Bump pyproject.toml to the version being released first.")
        return 1

    print("\nok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
