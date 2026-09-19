# -*- coding: utf-8 -*-

"""scripts/check_arch.py reads what a binary is, not what it is called.

Each release asset is named phabfive-<os>-<arch> from the build matrix, while
its architecture comes from whatever machine the runner label resolved to.
Nothing tied the two together, and v0.10.0 shipped an arm64 binary named
phabfive-macos-amd64: macos-14 and macos-latest are both arm64 labels, so the
row meant for Intel Macs built on Apple silicon. Downloading it on an Intel Mac
gives "Bad CPU type in executable".

scripts/smoke.py structurally cannot catch this. It runs each binary on the
runner that built it, where the architecture is native by definition. Only the
header of the finished artifact tells the truth, so these tests build headers
rather than binaries.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from check_arch import Unreadable, architecture, main  # noqa: E402


def elf(machine, *, big_endian=False):
    """The first 20 bytes of an ELF header: magic, class, byte order, e_machine."""
    order = "big" if big_endian else "little"
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2  # 64-bit
    header[5] = 2 if big_endian else 1
    header[18:20] = machine.to_bytes(2, order)
    return bytes(header)


def macho(cputype, *, big_endian=False):
    """A 64-bit Mach-O header: magic then cputype, in the file's byte order."""
    order = "big" if big_endian else "little"
    magic = b"\xfe\xed\xfa\xcf" if big_endian else b"\xcf\xfa\xed\xfe"
    return magic + cputype.to_bytes(4, order) + bytes(56)


def pe(machine, *, stub=0x80):
    """A DOS stub pointing at a COFF header, which opens with Machine."""
    header = bytearray(stub + 8)
    header[:2] = b"MZ"
    header[0x3C:0x40] = stub.to_bytes(4, "little")
    header[stub : stub + 4] = b"PE\x00\x00"
    header[stub + 4 : stub + 6] = machine.to_bytes(2, "little")
    return bytes(header)


def written(tmp_path, data):
    path = tmp_path / "phabfive-asset"
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize(
    "header, expected",
    [
        # Every format and architecture the release publishes.
        (elf(0x3E), "amd64"),
        (elf(0xB7), "arm64"),
        (macho(0x01000007), "amd64"),
        (macho(0x0100000C), "arm64"),
        (pe(0x8664), "amd64"),
        (pe(0xAA64), "arm64"),
        # Byte order is read from the file rather than assumed.
        (elf(0x3E, big_endian=True), "amd64"),
        (macho(0x0100000C, big_endian=True), "arm64"),
        # The COFF header is wherever the DOS stub says it is.
        (pe(0x8664, stub=0x100), "amd64"),
    ],
)
def test_the_architecture_is_read_from_the_header(tmp_path, header, expected):
    assert architecture(written(tmp_path, header)) == expected


def test_the_v0_10_0_mismatch_is_refused(tmp_path):
    """An arm64 Mach-O published under the amd64 name -- the shipped bug."""
    asset = written(tmp_path, macho(0x0100000C))

    assert main(["check_arch.py", "--expect", "amd64", asset]) == 1


@pytest.mark.parametrize(
    "header, expect",
    [
        (macho(0x01000007), "amd64"),
        (elf(0xB7), "arm64"),
        (pe(0xAA64), "arm64"),
    ],
)
def test_a_matching_architecture_passes(tmp_path, header, expect):
    assert main(["check_arch.py", "--expect", expect, written(tmp_path, header)]) == 0


@pytest.mark.parametrize(
    "header",
    [
        # A universal Mach-O holds several architectures and has its own magic.
        # PyInstaller does not build one here, so it is not a case to accept.
        b"\xca\xfe\xba\xbe" + bytes(60),
        # A shell script, which is what a failed build can leave behind.
        b"#!/bin/sh\n" + bytes(60),
        # 32-bit x86, which no row of the matrix should ever produce.
        elf(0x03),
        macho(0x00000007),
        pe(0x014C),
    ],
)
def test_anything_unrecognised_is_an_error_rather_than_a_pass(tmp_path, header):
    with pytest.raises(Unreadable):
        architecture(written(tmp_path, header))


def test_a_truncated_file_is_an_error(tmp_path):
    with pytest.raises(Unreadable):
        architecture(written(tmp_path, b"\x7fELF"))


def test_a_missing_file_exits_rather_than_traces(tmp_path):
    with pytest.raises(SystemExit):
        main(["check_arch.py", "--expect", "amd64", str(tmp_path / "nothing")])
