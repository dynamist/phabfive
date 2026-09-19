#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Refuse an executable whose architecture is not the one its name promises.

The release names each asset phabfive-<os>-<arch> from the build matrix, and
that name is the only thing a user has to go on when choosing a download. The
name comes from the matrix; the architecture comes from whatever machine the
runner label actually resolved to. Nothing tied the two together.

v0.10.0 shipped an arm64 binary as phabfive-macos-amd64, because macos-14 and
macos-latest are both arm64 labels -- the row meant to build for Intel Macs was
building on Apple silicon. An Intel Mac downloading it gets "Bad CPU type in
executable". Every job was green, and scripts/smoke.py could not have caught it:
it runs each binary on the runner that built it, which is native by definition
whatever that runner turns out to be. Only the finished artifact shows it.

    python3 scripts/check_arch.py --expect amd64 dist/phabfive-macos-amd64

Reads the file header rather than shelling out to file(1), which the Windows
runners do not have. Stdlib only, like the other release scripts.
"""

import argparse
import sys

# ELF e_machine, at offset 18, for the two architectures released.
ELF_MACHINES = {0x3E: "amd64", 0xB7: "arm64"}

# Mach-O cputype, at offset 4. The 0x01000000 bit is CPU_ARCH_ABI64.
MACHO_CPUTYPES = {0x01000007: "amd64", 0x0100000C: "arm64"}

# PE IMAGE_FILE_HEADER Machine, at the COFF header.
PE_MACHINES = {0x8664: "amd64", 0xAA64: "arm64"}

# Both byte orders of the 64-bit Mach-O magic. A universal ("fat") binary has
# its own magic and holds several architectures, which is not what PyInstaller
# builds here -- reporting it as unknown is right, not a case to support.
MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")


class Unreadable(Exception):
    """The file is not an executable this knows how to read."""


def little(data: bytes) -> int:
    return int.from_bytes(data, "little")


def elf_arch(header: bytes) -> str:
    """ELF: e_machine is a 2-byte field at offset 18, in the file's byte order."""
    order = "little" if header[5] == 1 else "big"
    machine = int.from_bytes(header[18:20], order)
    try:
        return ELF_MACHINES[machine]
    except KeyError:
        raise Unreadable(f"unknown ELF e_machine 0x{machine:x}")


def macho_arch(header: bytes) -> str:
    """Mach-O: cputype is a 4-byte field straight after the magic."""
    order = "little" if header[:4] == b"\xcf\xfa\xed\xfe" else "big"
    cputype = int.from_bytes(header[4:8], order)
    try:
        return MACHO_CPUTYPES[cputype]
    except KeyError:
        raise Unreadable(f"unknown Mach-O cputype 0x{cputype:x}")


def pe_arch(header: bytes) -> str:
    """PE: the DOS stub points at the COFF header, which opens with Machine."""
    offset = little(header[0x3C:0x40])
    if header[offset : offset + 4] != b"PE\x00\x00":
        raise Unreadable("no PE signature where the DOS header points")

    machine = little(header[offset + 4 : offset + 6])
    try:
        return PE_MACHINES[machine]
    except KeyError:
        raise Unreadable(f"unknown PE machine 0x{machine:x}")


def architecture(path: str) -> str:
    """The architecture the binary at `path` was built for."""
    with open(path, "rb") as handle:
        # Enough for the DOS stub and a PE header at any plausible offset.
        header = handle.read(4096)

    if len(header) < 64:
        raise Unreadable(f"only {len(header)} bytes long")

    if header[:4] == b"\x7fELF":
        return elf_arch(header)
    if header[:4] in MACHO_MAGICS:
        return macho_arch(header)
    if header[:2] == b"MZ":
        return pe_arch(header)

    raise Unreadable(f"not an ELF, Mach-O or PE executable: {header[:4]!r}")


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--expect", required=True, choices=sorted(set(ELF_MACHINES.values()))
    )
    parser.add_argument("executable")
    args = parser.parse_args(argv[1:])

    try:
        found = architecture(args.executable)
    except OSError as error:
        sys.exit(f"cannot read {args.executable}: {error}")
    except Unreadable as error:
        sys.exit(f"cannot read the architecture of {args.executable}: {error}")

    print(f"{args.executable} is {found}, expected {args.expect}")

    if found != args.expect:
        print()
        print(f"The release would publish this {found} binary as {args.expect}.")
        print("Check which architecture the runner label resolves to.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
