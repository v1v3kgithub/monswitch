"""EDID parsing — bytes in, monitor identity out.

Reads the manufacturer, model and serial from a raw EDID blob, as published by the kernel at
``/sys/class/drm/card*/card*-*/edid``. Pure and side-effect free: no I/O, no subprocess, no
``ddcutil``. See ADR-0010 (sysfs-first resolution) and ADR-0013 (identity).

Only the 128-byte base block is interpreted. Extension blocks (CTA-861 and friends) carry
timing and audio data, nothing identifying, so they are ignored.

The serial is the awkward part. Two places can hold one:

* the four-byte field at offset 12, which is a *number*, and
* a "display product serial number" descriptor tagged ``0xff``, which is a *string*.

Neither is reliably present. On the reference HP Z38c the offset-12 field reads ``0x01010101``
— a placeholder — while the real serial ``DEMOSERIAL`` lives in the ``0xff`` descriptor. On the
reference LG laptop panel there are no string descriptors at all and offset 12 is zero, so the
panel has no serial to key on. Both cases are covered by fixtures.

This module reports what the EDID says and normalises placeholders to ``None``. It does not
decide what to do about a missing serial; that is identity-key derivation, which belongs to
``models`` per ADR-0013.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["EdidError", "EdidIdentity", "parse"]

# The fixed 8-byte pattern every EDID begins with.
_MAGIC = b"\x00\xff\xff\xff\xff\xff\xff\x00"

_BASE_BLOCK_LEN = 128

# Byte offsets into the base block.
_OFF_MFG_ID = 8  # 2 bytes, big endian, three 5-bit letters
_OFF_PRODUCT_CODE = 10  # 2 bytes, little endian
_OFF_SERIAL_NUMBER = 12  # 4 bytes, little endian
_OFF_DESCRIPTORS = 54  # four 18-byte blocks, ending at 126

_DESCRIPTOR_LEN = 18
_DESCRIPTOR_COUNT = 4

# Descriptor type tags we care about. The others are timing or range limits.
_TAG_SERIAL = 0xFF
_TAG_NAME = 0xFC

# Text descriptors are terminated by 0x0a and padded with 0x20.
_TEXT_TERMINATOR = 0x0A


class EdidError(ValueError):
    """Raised when a blob is not a usable EDID.

    A subclass of :class:`ValueError` because that is what a parser rejecting its input
    should be. ADR-0011 calls for typed domain errors surfaced per frontend; when the error
    hierarchy lands with the ``ddcutil`` wrapper, this should become part of it.
    """


@dataclass(frozen=True, slots=True)
class EdidIdentity:
    """What an EDID says about which monitor it belongs to.

    ``manufacturer`` is the three-letter PNP ID (``HPN``, ``LGD``) and is always present in a
    valid EDID. ``product_code`` is the vendor's numeric model ID, also always present, and is
    a useful tiebreaker when ``model`` is absent. ``model`` and ``serial`` are ``None`` when
    the EDID does not carry them, or carries only a placeholder.
    """

    manufacturer: str
    product_code: int
    model: str | None
    serial: str | None


def parse(data: bytes) -> EdidIdentity:
    """Parse an EDID blob into an :class:`EdidIdentity`.

    Accepts the full blob including any extension blocks; only the base block is read.

    Raises:
        EdidError: if the blob is too short, lacks the EDID magic header, or fails its
            base-block checksum.
    """
    if len(data) < _BASE_BLOCK_LEN:
        raise EdidError(f"EDID too short: {len(data)} bytes, need at least {_BASE_BLOCK_LEN}")

    base = data[:_BASE_BLOCK_LEN]

    if not base.startswith(_MAGIC):
        raise EdidError(f"not an EDID: header is {base[:8].hex()}, expected {_MAGIC.hex()}")

    # The base block's bytes sum to a multiple of 256; the final byte exists to make that so.
    if sum(base) % 256 != 0:
        raise EdidError("EDID base block checksum invalid")

    return EdidIdentity(
        manufacturer=_manufacturer(base),
        product_code=int.from_bytes(base[_OFF_PRODUCT_CODE : _OFF_PRODUCT_CODE + 2], "little"),
        model=_descriptor_text(base, _TAG_NAME),
        serial=_serial(base),
    )


def _manufacturer(base: bytes) -> str:
    """Decode the PNP manufacturer ID: three 5-bit letters packed big-endian into 2 bytes.

    Bit 15 is reserved and zero, then three 5-bit groups where 1 means 'A'.
    """
    packed = int.from_bytes(base[_OFF_MFG_ID : _OFF_MFG_ID + 2], "big")
    letters = [chr(((packed >> shift) & 0x1F) + ord("A") - 1) for shift in (10, 5, 0)]
    return "".join(letters)


def _serial(base: bytes) -> str | None:
    """The serial, preferring the ``0xff`` string descriptor over the numeric field.

    The string descriptor is what vendors actually put human-readable serials in. The numeric
    field is used only when no descriptor carries one, and only when it is not a placeholder.
    """
    from_descriptor = _descriptor_text(base, _TAG_SERIAL)
    if from_descriptor is not None:
        return from_descriptor

    raw = base[_OFF_SERIAL_NUMBER : _OFF_SERIAL_NUMBER + 4]
    if _is_placeholder(raw):
        return None
    return str(int.from_bytes(raw, "little"))


def _is_placeholder(raw: bytes) -> bool:
    """Whether a numeric serial field carries no real information.

    Zero means "not set". An all-identical-bytes value such as ``0x01010101`` (the reference
    HP panel) or ``0xffffffff`` is a factory filler rather than a serial. ADR-0013 requires
    both be treated as absent.
    """
    return len(set(raw)) == 1


def _descriptor_text(base: bytes, tag: int) -> str | None:
    """Extract the text of the first descriptor with ``tag``, or ``None`` if there is none.

    A text descriptor is 18 bytes: three zero bytes, the tag, a reserved zero, then 13 bytes
    of text terminated by ``0x0a`` and space-padded. Returns ``None`` for a descriptor whose
    text is empty or whitespace only, since that is as good as absent.
    """
    for index in range(_DESCRIPTOR_COUNT):
        start = _OFF_DESCRIPTORS + index * _DESCRIPTOR_LEN
        block = base[start : start + _DESCRIPTOR_LEN]
        if len(block) < _DESCRIPTOR_LEN:
            break
        # A leading non-zero pixel clock marks a timing descriptor, not a text one.
        if block[0:3] != b"\x00\x00\x00" or block[3] != tag:
            continue

        text = block[5:_DESCRIPTOR_LEN]
        if _TEXT_TERMINATOR in text:
            text = text[: text.index(_TEXT_TERMINATOR)]
        # latin-1 cannot fail, and keeps any stray high bytes legible rather than raising.
        decoded = text.decode("latin-1").strip()
        if decoded:
            return decoded
    return None
