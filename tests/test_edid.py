"""Tests for the EDID parser.

Written as :mod:`unittest` test cases rather than bare pytest functions, deliberately: the
core is standard-library only, and on an externally-managed distribution (PEP 668, see
ADR-0012) pytest may not be installed. These run either way::

    .venv/bin/python -m pytest tests/        # with the dev venv
    python3 -m unittest discover tests/      # no dependencies at all

The two binary fixtures are real EDIDs captured from the reference machine on 2026-11-09, and
between them cover both serial cases that ADR-0013 has to handle:

* ``hp-z38c-dp1.bin`` — serial in a ``0xff`` descriptor, offset-12 field a ``0x01010101``
  placeholder.
* ``lgd-edp1.bin`` — no string descriptors at all, offset-12 field zero: a panel with no
  serial to key on.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from monswitch.core.edid import EdidError, EdidIdentity, parse

FIXTURES = Path(__file__).parent / "fixtures" / "edid"

_MAGIC = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def build_edid(
    *,
    manufacturer: str = "ABC",
    product_code: int = 0x1234,
    serial_number: bytes = b"\x00\x00\x00\x00",
    descriptors: list[tuple[int, bytes]] | None = None,
    fix_checksum: bool = True,
) -> bytes:
    """Build a synthetic 128-byte base-block EDID for cases the real fixtures do not cover.

    ``descriptors`` is a list of ``(tag, text)`` pairs laid out from offset 54; text is
    terminated with ``0x0a`` and space-padded to 13 bytes, as the spec requires.
    """
    base = bytearray(128)
    base[0:8] = _MAGIC

    packed = 0
    for shift, letter in zip((10, 5, 0), manufacturer):
        packed |= (ord(letter) - ord("A") + 1) << shift
    base[8:10] = packed.to_bytes(2, "big")

    base[10:12] = product_code.to_bytes(2, "little")
    base[12:16] = serial_number

    for index, (tag, text) in enumerate(descriptors or []):
        start = 54 + index * 18
        payload = text + b"\n" if len(text) < 13 else text[:13]
        base[start : start + 5] = bytes([0x00, 0x00, 0x00, tag, 0x00])
        base[start + 5 : start + 18] = payload.ljust(13, b" ")

    if fix_checksum:
        base[127] = (-sum(base[:127])) % 256
    return bytes(base)


class RealFixturesTest(unittest.TestCase):
    """The parser against EDIDs actually read from the reference machine."""

    def test_hp_z38c_reads_serial_from_descriptor(self) -> None:
        identity = parse((FIXTURES / "hp-z38c-dp1.bin").read_bytes())

        self.assertEqual(identity.manufacturer, "HPN")
        self.assertEqual(identity.product_code, 13327)
        self.assertEqual(identity.model, "HP Z38c")
        # The real serial, from the 0xff descriptor — not the 0x01010101 at offset 12.
        self.assertEqual(identity.serial, "DEMOSERIAL")

    def test_lgd_panel_has_no_model_or_serial(self) -> None:
        """A panel with no string descriptors and a zeroed serial field yields None for both.

        This is the case ADR-0013's identity fallback chain exists for.
        """
        identity = parse((FIXTURES / "lgd-edp1.bin").read_bytes())

        self.assertEqual(identity.manufacturer, "LGD")
        self.assertEqual(identity.product_code, 1826)
        self.assertIsNone(identity.model)
        self.assertIsNone(identity.serial)

    def test_extension_blocks_are_ignored(self) -> None:
        """Both fixtures are 256 bytes; parsing must not depend on or trip over the extension."""
        full = (FIXTURES / "hp-z38c-dp1.bin").read_bytes()
        self.assertEqual(len(full), 256)
        self.assertEqual(parse(full), parse(full[:128]))


class ManufacturerTest(unittest.TestCase):
    def test_decodes_packed_five_bit_letters(self) -> None:
        self.assertEqual(parse(build_edid(manufacturer="HPN")).manufacturer, "HPN")
        self.assertEqual(parse(build_edid(manufacturer="AAA")).manufacturer, "AAA")
        self.assertEqual(parse(build_edid(manufacturer="ZZZ")).manufacturer, "ZZZ")


class SerialTest(unittest.TestCase):
    def test_numeric_field_used_when_no_descriptor(self) -> None:
        """Panels that put a real serial in the numeric field must not lose it."""
        edid = build_edid(serial_number=(123456789).to_bytes(4, "little"))
        self.assertEqual(parse(edid).serial, "123456789")

    def test_descriptor_wins_over_numeric_field(self) -> None:
        edid = build_edid(
            serial_number=(999).to_bytes(4, "little"),
            descriptors=[(0xFF, b"REAL123")],
        )
        self.assertEqual(parse(edid).serial, "REAL123")

    def test_zero_numeric_field_is_absent(self) -> None:
        self.assertIsNone(parse(build_edid(serial_number=b"\x00\x00\x00\x00")).serial)

    def test_repeated_byte_numeric_field_is_placeholder(self) -> None:
        """0x01010101 (the HP panel) and 0xffffffff are factory filler, not serials."""
        self.assertIsNone(parse(build_edid(serial_number=b"\x01\x01\x01\x01")).serial)
        self.assertIsNone(parse(build_edid(serial_number=b"\xff\xff\xff\xff")).serial)

    def test_whitespace_only_descriptor_is_absent(self) -> None:
        """An empty descriptor must fall through, not yield an empty-string serial."""
        edid = build_edid(
            serial_number=b"\x00\x00\x00\x00", descriptors=[(0xFF, b"   ")]
        )
        self.assertIsNone(parse(edid).serial)

    def test_whitespace_only_descriptor_falls_back_to_numeric_field(self) -> None:
        edid = build_edid(
            serial_number=(42).to_bytes(4, "little"), descriptors=[(0xFF, b"  ")]
        )
        self.assertEqual(parse(edid).serial, "42")


class ModelTest(unittest.TestCase):
    def test_strips_terminator_and_padding(self) -> None:
        self.assertEqual(parse(build_edid(descriptors=[(0xFC, b"HP Z38c")])).model, "HP Z38c")

    def test_full_thirteen_byte_text_without_terminator(self) -> None:
        """A name filling all 13 bytes has no 0x0a terminator; it must not be truncated."""
        edid = build_edid(descriptors=[(0xFC, b"ABCDEFGHIJKLM")])
        self.assertEqual(parse(edid).model, "ABCDEFGHIJKLM")

    def test_absent_when_no_name_descriptor(self) -> None:
        self.assertIsNone(parse(build_edid(descriptors=[(0xFF, b"SN1")])).model)

    def test_timing_descriptor_is_not_read_as_text(self) -> None:
        """A descriptor with a non-zero pixel clock is timing data and must be skipped."""
        edid = bytearray(build_edid(descriptors=[(0xFC, b"REAL")]))
        # Make the *first* descriptor slot a timing block; the name moves to the second.
        edid[54:72] = bytes([0x01, 0x02, 0x03]) + bytes(15)
        edid[72:90] = bytes([0x00, 0x00, 0x00, 0xFC, 0x00]) + b"REAL\n".ljust(13, b" ")
        edid[127] = (-sum(edid[:127])) % 256
        self.assertEqual(parse(bytes(edid)).model, "REAL")


class ValidationTest(unittest.TestCase):
    def test_rejects_short_blob(self) -> None:
        with self.assertRaises(EdidError) as caught:
            parse(b"\x00" * 64)
        self.assertIn("too short", str(caught.exception))

    def test_rejects_empty_blob(self) -> None:
        """A disconnected connector's edid file reads as zero bytes."""
        with self.assertRaises(EdidError):
            parse(b"")

    def test_rejects_bad_magic(self) -> None:
        edid = bytearray(build_edid())
        edid[0] = 0xDE
        with self.assertRaises(EdidError) as caught:
            parse(bytes(edid))
        self.assertIn("not an EDID", str(caught.exception))

    def test_rejects_bad_checksum(self) -> None:
        edid = bytearray(build_edid())
        edid[127] = (edid[127] + 1) % 256
        with self.assertRaises(EdidError) as caught:
            parse(bytes(edid))
        self.assertIn("checksum", str(caught.exception))

    def test_edid_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(EdidError, ValueError))


class IdentityTest(unittest.TestCase):
    def test_is_frozen_and_comparable(self) -> None:
        """Identities are used as dict keys and compared, so they must be hashable values."""
        a = parse(build_edid(manufacturer="ABC"))
        b = parse(build_edid(manufacturer="ABC"))
        self.assertEqual(a, b)
        self.assertEqual(len({a, b}), 1)
        with self.assertRaises(Exception):
            a.serial = "nope"  # type: ignore[misc]

    def test_parse_is_side_effect_free_on_input(self) -> None:
        data = build_edid(descriptors=[(0xFC, b"X")])
        before = bytes(data)
        parse(data)
        self.assertEqual(data, before)

    def test_identity_type(self) -> None:
        self.assertIsInstance(parse(build_edid()), EdidIdentity)


if __name__ == "__main__":
    unittest.main()
