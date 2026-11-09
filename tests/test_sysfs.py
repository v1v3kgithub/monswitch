"""Tests for sysfs monitor resolution.

Driven against synthetic DRM trees built in a tmpdir, so the whole matrix of driver
behaviours is testable on any machine — including the NVIDIA case (no ``ddc`` symlinks) that
the reference machine cannot reproduce.

One test reads the real ``/sys/class/drm`` and is skipped where it is absent or shaped
differently; it is a smoke check that the synthetic trees resemble reality, not a
correctness test.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monswitch.core.sysfs import (
    DEFAULT_DRM_ROOT,
    DetectedMonitor,
    SkipReason,
    resolve,
)

FIXTURES = Path(__file__).parent / "fixtures" / "edid"
HP_EDID = (FIXTURES / "hp-z38c-dp1.bin").read_bytes()
LGD_EDID = (FIXTURES / "lgd-edp1.bin").read_bytes()


def make_connector(
    root: Path,
    name: str,
    *,
    status: str = "connected",
    edid: bytes = HP_EDID,
    bus: int | None = 16,
    ddc_target: str | None = None,
) -> Path:
    """Create one synthetic DRM connector directory.

    Mirrors the real layout: ``ddc`` is a *relative* symlink to a sibling ``i2c-N``
    directory inside the connector dir, which is how i915 publishes it.
    """
    connector = root / name
    connector.mkdir(parents=True)
    (connector / "status").write_text(status + "\n")
    (connector / "edid").write_bytes(edid)

    target = ddc_target if ddc_target is not None else (f"i2c-{bus}" if bus is not None else None)
    if target is not None:
        (connector / target).mkdir(exist_ok=True)
        os.symlink(target, connector / "ddc")
    return connector


class TempDrmTree(unittest.TestCase):
    """Base class giving each test an empty synthetic DRM root."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class ResolveTest(TempDrmTree):
    def test_finds_external_monitor_with_bus_and_identity(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=16, edid=HP_EDID)

        result = resolve(self.root)

        self.assertEqual(len(result.monitors), 1)
        monitor = result.monitors[0]
        self.assertIsInstance(monitor, DetectedMonitor)
        self.assertEqual(monitor.bus, 16)
        self.assertEqual(monitor.connector, "DP-1")
        self.assertEqual(monitor.card, "card1")
        self.assertEqual(monitor.drm_name, "card1-DP-1")
        self.assertEqual(monitor.identity.serial, "DEMOSERIAL")
        self.assertEqual(monitor.identity.model, "HP Z38c")

    def test_connector_name_with_hyphens_is_preserved(self) -> None:
        """Only the cardN- prefix is stripped; HDMI-A-1 must survive intact."""
        make_connector(self.root, "card1-HDMI-A-1", bus=7)

        monitor = resolve(self.root).monitors[0]

        self.assertEqual(monitor.connector, "HDMI-A-1")
        self.assertEqual(monitor.drm_name, "card1-HDMI-A-1")

    def test_results_are_ordered_by_connector_name(self) -> None:
        make_connector(self.root, "card1-DP-3", bus=18)
        make_connector(self.root, "card1-DP-1", bus=16)
        make_connector(self.root, "card1-DP-2", bus=17)

        buses = [m.bus for m in resolve(self.root).monitors]

        self.assertEqual(buses, [16, 17, 18])

    def test_nested_card_layout_is_supported(self) -> None:
        """Some kernels nest connectors under the card dir: card1/card1-DP-1."""
        make_connector(self.root / "card1", "card1-DP-1", bus=16)

        result = resolve(self.root)

        self.assertEqual(len(result.monitors), 1)
        self.assertEqual(result.monitors[0].bus, 16)

    def test_multiple_monitors_on_distinct_buses(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=16, edid=HP_EDID)
        make_connector(self.root, "card1-DP-2", bus=17, edid=HP_EDID)

        result = resolve(self.root)

        self.assertEqual([m.bus for m in result.monitors], [16, 17])


class FilteringTest(TempDrmTree):
    def test_disconnected_connector_is_skipped(self) -> None:
        make_connector(self.root, "card1-DP-2", status="disconnected", edid=b"")

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertIn(("card1-DP-2", SkipReason.DISCONNECTED), result.skipped)

    def test_internal_panel_is_skipped(self) -> None:
        """eDP has no DDC/CI input switching, so it is never a candidate."""
        make_connector(self.root, "card1-eDP-1", bus=15, edid=LGD_EDID)

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertIn(("card1-eDP-1", SkipReason.INTERNAL_PANEL), result.skipped)

    def test_other_internal_panel_types_are_skipped(self) -> None:
        make_connector(self.root, "card1-LVDS-1", bus=1)
        make_connector(self.root, "card1-DSI-1", bus=2)

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertEqual(len(result.skipped), 2)

    def test_connector_without_ddc_link_is_skipped(self) -> None:
        """The NVIDIA proprietary case: connected panel, no published I2C adapter."""
        make_connector(self.root, "card1-DP-1", bus=None)

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertIn(("card1-DP-1", SkipReason.NO_DDC_LINK), result.skipped)

    def test_ddc_link_not_naming_an_adapter_is_skipped(self) -> None:
        make_connector(self.root, "card1-DP-1", ddc_target="not-an-i2c-dir")

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertIn(("card1-DP-1", SkipReason.UNREADABLE_DDC_LINK), result.skipped)

    def test_empty_edid_is_skipped(self) -> None:
        """A connector can read 'connected' while its edid is still empty."""
        make_connector(self.root, "card1-DP-1", bus=16, edid=b"")

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertIn(("card1-DP-1", SkipReason.NO_EDID), result.skipped)

    def test_malformed_edid_is_skipped_not_raised(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=16, edid=b"\xde\xad\xbe\xef" * 32)

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertIn(("card1-DP-1", SkipReason.BAD_EDID), result.skipped)

    def test_one_broken_connector_does_not_hide_the_others(self) -> None:
        """A machine with a bad connector must still switch its working ones."""
        make_connector(self.root, "card1-DP-1", bus=16, edid=b"garbage")
        make_connector(self.root, "card1-DP-2", bus=17, edid=HP_EDID)

        result = resolve(self.root)

        self.assertEqual(len(result.monitors), 1)
        self.assertEqual(result.monitors[0].bus, 17)
        self.assertIn(("card1-DP-1", SkipReason.BAD_EDID), result.skipped)

    def test_non_connector_entries_are_ignored(self) -> None:
        (self.root / "card1").mkdir()
        (self.root / "version").write_text("drm 1.1.0\n")
        make_connector(self.root, "card1-DP-1", bus=16)

        self.assertEqual(len(resolve(self.root).monitors), 1)


class FallbackSignalTest(TempDrmTree):
    def test_no_fallback_when_a_monitor_is_found(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=16)

        self.assertFalse(resolve(self.root).needs_fallback)

    def test_fallback_when_no_connector_publishes_a_ddc_link(self) -> None:
        """Driver does not expose the mapping -> ddcutil detect is the only route."""
        make_connector(self.root, "card1-DP-1", bus=None)
        make_connector(self.root, "card1-DP-2", bus=None)

        result = resolve(self.root)

        self.assertFalse(result.ddc_links_present)
        self.assertTrue(result.needs_fallback)

    def test_no_fallback_when_links_work_but_nothing_is_plugged_in(self) -> None:
        """Must not trigger a 1.5 s detect probe just because no monitor is attached."""
        make_connector(self.root, "card1-DP-1", status="disconnected", edid=b"")
        make_connector(self.root, "card1-eDP-1", bus=15, edid=LGD_EDID)

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertFalse(result.needs_fallback)

    def test_fallback_when_drm_root_is_absent(self) -> None:
        result = resolve(self.root / "nonexistent")

        self.assertFalse(result.root_present)
        self.assertTrue(result.needs_fallback)
        self.assertEqual(result.monitors, ())

    def test_internal_panel_link_counts_as_driver_support(self) -> None:
        """A laptop with only its own panel attached has a working driver, not a broken one.

        The signal is about whether the driver publishes the mapping, so an eDP link counts
        even though eDP itself is never a switching candidate.
        """
        make_connector(self.root, "card1-eDP-1", bus=15, edid=LGD_EDID)

        result = resolve(self.root)

        self.assertEqual(result.monitors, ())
        self.assertTrue(result.ddc_links_present)
        self.assertFalse(result.needs_fallback)

    def test_links_counted_for_disconnected_ports(self) -> None:
        """Real sysfs publishes ddc for unplugged ports too (DP-2 -> i2c-17 on the dev box)."""
        make_connector(self.root, "card1-DP-2", status="disconnected", edid=b"", bus=17)

        self.assertEqual(resolve(self.root).ddc_links_seen, 1)

    def test_links_present_when_edid_is_merely_unparseable(self) -> None:
        """A bad EDID is not a missing-driver-support signal; detect would not help."""
        make_connector(self.root, "card1-DP-1", bus=16, edid=b"garbage")

        self.assertTrue(resolve(self.root).ddc_links_present)


@unittest.skipUnless(
    DEFAULT_DRM_ROOT.is_dir(), f"{DEFAULT_DRM_ROOT} not present on this machine"
)
class RealSysfsSmokeTest(unittest.TestCase):
    """Sanity check against the live tree. Read-only; asserts shape, not specific hardware."""

    def test_resolves_without_error(self) -> None:
        result = resolve()

        for monitor in result.monitors:
            self.assertGreaterEqual(monitor.bus, 0)
            self.assertTrue(monitor.connector)
            self.assertFalse(monitor.connector.startswith("eDP"))
            self.assertTrue(monitor.identity.manufacturer)

    def test_skipped_entries_carry_a_reason(self) -> None:
        for name, reason in resolve().skipped:
            self.assertTrue(name)
            self.assertTrue(reason)


if __name__ == "__main__":
    unittest.main()
