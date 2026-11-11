"""Tests for the discovery merge.

Everything is driven through a synthetic sysfs tree plus a fake ``Ddcutil``, so the full
matrix runs with no hardware: panels whose live input is absent from capabilities, failed
reads, the NVIDIA detect-fallback path, key collisions, and the firmware-unnamed-code rule.

The reference machine's situation — live ``0x13`` absent from a capability list that instead
offers an unnamed ``1f`` — is reproduced exactly, since that is the case the whole provenance
model exists for.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from monswitch.core.ddc import (
    CapabilityInput,
    CommandResult,
    Ddcutil,
    DetectedDisplay,
    I2cPermissionDenied,
    NoMonitorOnBus,
)
from monswitch.core.discovery import discover
from monswitch.core.models import Provenance

from test_sysfs import HP_EDID, LGD_EDID, make_connector

FIXTURES = Path(__file__).parent / "fixtures" / "ddcutil"


class FakeDdcutil(Ddcutil):
    """A Ddcutil that answers from canned data instead of running anything.

    Subclasses rather than duck-types so that any method the real wrapper gains is inherited,
    and so a test cannot accidentally pass something that is not a Ddcutil.
    """

    def __init__(
        self,
        *,
        live: dict[int, int] | None = None,
        capabilities: dict[int, Sequence[CapabilityInput]] | None = None,
        detect_result: Sequence[DetectedDisplay] = (),
        live_errors: dict[int, Exception] | None = None,
        capability_errors: dict[int, Exception] | None = None,
        detect_error: Exception | None = None,
    ) -> None:
        object.__setattr__(self, "_live", live or {})
        object.__setattr__(self, "_capabilities", capabilities or {})
        object.__setattr__(self, "_detect_result", tuple(detect_result))
        object.__setattr__(self, "_live_errors", live_errors or {})
        object.__setattr__(self, "_capability_errors", capability_errors or {})
        object.__setattr__(self, "_detect_error", detect_error)
        object.__setattr__(self, "capability_calls", [])
        object.__setattr__(self, "executable", "fake-ddcutil")
        object.__setattr__(self, "timeout", 1.0)
        object.__setattr__(self, "runner", _unused_runner)

    def getvcp_input(self, bus: int) -> int:
        if bus in self._live_errors:
            raise self._live_errors[bus]
        return self._live[bus]

    def capabilities_inputs(self, bus: int) -> tuple[CapabilityInput, ...]:
        self.capability_calls.append(bus)
        if bus in self._capability_errors:
            raise self._capability_errors[bus]
        return tuple(self._capabilities.get(bus, ()))

    def detect(self, *, terse: bool = True) -> tuple[DetectedDisplay, ...]:
        if self._detect_error is not None:
            raise self._detect_error
        return self._detect_result


def _unused_runner(argv: Sequence[str], timeout: float) -> CommandResult:
    raise AssertionError(f"FakeDdcutil must not execute anything, got {list(argv)}")


# The reference panel's real capability list: two named inputs plus firmware junk.
REFERENCE_CAPS = (
    CapabilityInput(code=0x0F, name="DisplayPort-1"),
    CapabilityInput(code=0x11, name="HDMI-1"),
    CapabilityInput(code=0x1F, name=None),
)


class DiscoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def one_hp(self, bus: int = 16, connector: str = "card1-DP-1") -> None:
        make_connector(self.root, connector, bus=bus, edid=HP_EDID)


class ReferenceMachineTest(DiscoveryTestCase):
    """Reproduces the exact situation on the dev machine."""

    def setUp(self) -> None:
        super().setUp()
        self.one_hp()
        self.ddcutil = FakeDdcutil(live={16: 0x13}, capabilities={16: REFERENCE_CAPS})

    def test_finds_the_monitor(self) -> None:
        result = discover(self.ddcutil, root=self.root, include_capabilities=True)

        self.assertEqual(len(result.monitors), 1)
        monitor = result.monitors[0].monitor
        self.assertEqual(monitor.display_name, "HP Z38c")
        self.assertEqual(monitor.bus, 16)
        self.assertEqual(result.monitors[0].key, "DEMOSERIAL")

    def test_live_input_becomes_the_host_input(self) -> None:
        monitor = discover(self.ddcutil, root=self.root).monitors[0].monitor

        self.assertEqual(monitor.host_input, 0x13)
        self.assertTrue(monitor.is_switchable)

    def test_live_code_absent_from_capabilities_is_still_listed_and_verified(self) -> None:
        """The founding bug: 0x13 is missing from capabilities but is the real input."""
        monitor = discover(
            self.ddcutil, root=self.root, include_capabilities=True
        ).monitors[0].monitor

        usbc = monitor.input_for(0x13)

        self.assertIsNotNone(usbc)
        assert usbc is not None
        self.assertEqual(usbc.provenance, Provenance.LIVE)
        self.assertTrue(usbc.is_verified)

    def test_capabilities_entries_are_reported_not_verified(self) -> None:
        monitor = discover(
            self.ddcutil, root=self.root, include_capabilities=True
        ).monitors[0].monitor

        dp = monitor.input_for(0x0F)

        assert dp is not None
        self.assertEqual(dp.provenance, Provenance.REPORTED)
        self.assertFalse(dp.is_verified)
        self.assertEqual(dp.name, "DisplayPort-1")

    def test_firmware_unnamed_code_stays_guessed_and_unnamed(self) -> None:
        """ADR-0009: 1f must not be promoted, and must not be given an invented name."""
        monitor = discover(
            self.ddcutil, root=self.root, include_capabilities=True
        ).monitors[0].monitor

        junk = monitor.input_for(0x1F)

        assert junk is not None
        self.assertEqual(junk.provenance, Provenance.GUESSED)
        self.assertIsNone(junk.name)
        self.assertEqual(junk.label, "0x1f")

    def test_no_warnings_on_a_healthy_machine(self) -> None:
        result = discover(self.ddcutil, root=self.root, include_capabilities=True)

        self.assertEqual(result.warnings, ())
        self.assertFalse(result.used_detect_fallback)


class NoSpeculativeInputsTest(DiscoveryTestCase):
    """ADR-0014: nothing appears that the monitor or the user did not mention.

    The built-in code table used to pad every list with 0x10, 0x12 and 0x1b. On the reference
    panel none of those exist, and selecting a non-existent input is the case that blanks the
    screen with no software route back -- so the padding manufactured precisely the most
    dangerous rows.
    """

    def test_only_reported_and_live_inputs_appear(self) -> None:
        self.one_hp()

        monitor = discover(
            FakeDdcutil(live={16: 0x13}, capabilities={16: REFERENCE_CAPS}),
            root=self.root,
            include_capabilities=True,
        ).monitors[0].monitor

        self.assertEqual([i.code for i in monitor.inputs], [0x0F, 0x11, 0x13, 0x1F])

    def test_codes_the_panel_never_mentioned_are_absent(self) -> None:
        self.one_hp()

        monitor = discover(
            FakeDdcutil(live={16: 0x13}, capabilities={16: REFERENCE_CAPS}),
            root=self.root,
            include_capabilities=True,
        ).monitors[0].monitor

        for absent in (0x10, 0x12, 0x1B):
            with self.subTest(code=absent):
                self.assertIsNone(monitor.input_for(absent))

    def test_without_capabilities_only_the_live_input_appears(self) -> None:
        """The honest minimum: one row, because one input is all the monitor has admitted."""
        self.one_hp()

        monitor = discover(
            FakeDdcutil(live={16: 0x13}), root=self.root
        ).monitors[0].monitor

        self.assertEqual([i.code for i in monitor.inputs], [0x13])

    def test_live_only_code_carries_no_invented_name(self) -> None:
        """0x13 is vendor-specific; naming it 'USB-C' would be a guess about this panel."""
        self.one_hp()

        monitor = discover(
            FakeDdcutil(live={16: 0x13}), root=self.root
        ).monitors[0].monitor

        usbc = monitor.input_for(0x13)

        assert usbc is not None
        self.assertIsNone(usbc.name)
        self.assertEqual(usbc.label, "0x13")

    def test_a_config_nickname_is_how_a_name_is_supplied(self) -> None:
        self.one_hp()

        monitor = discover(
            FakeDdcutil(live={16: 0x13}),
            root=self.root,
            confirmed_inputs={"DEMOSERIAL": {0x13: "This Laptop"}},
        ).monitors[0].monitor

        usbc = monitor.input_for(0x13)

        assert usbc is not None
        self.assertEqual(usbc.label, "This Laptop")


class ConfirmedInputsTest(DiscoveryTestCase):
    def test_confirmed_entries_outrank_everything(self) -> None:
        self.one_hp()

        monitor = discover(
            FakeDdcutil(live={16: 0x13}, capabilities={16: REFERENCE_CAPS}),
            root=self.root,
            include_capabilities=True,
            confirmed_inputs={"DEMOSERIAL": {0x0F: "Desktop PC"}},
        ).monitors[0].monitor

        dp = monitor.input_for(0x0F)

        assert dp is not None
        self.assertEqual(dp.provenance, Provenance.CONFIRMED)
        self.assertEqual(dp.name, "Desktop PC")
        self.assertTrue(dp.is_verified)

    def test_confirmed_name_overrides_firmware_disclaimer(self) -> None:
        """A human confirming a code deliberately outranks the firmware's shrug."""
        self.one_hp()

        monitor = discover(
            FakeDdcutil(live={16: 0x13}, capabilities={16: REFERENCE_CAPS}),
            root=self.root,
            include_capabilities=True,
            confirmed_inputs={"DEMOSERIAL": {0x1F: "Second USB-C"}},
        ).monitors[0].monitor

        junk = monitor.input_for(0x1F)

        assert junk is not None
        self.assertEqual(junk.provenance, Provenance.CONFIRMED)
        self.assertEqual(junk.name, "Second USB-C")

    def test_confirmed_map_for_another_monitor_is_not_applied(self) -> None:
        self.one_hp()

        monitor = discover(
            FakeDdcutil(live={16: 0x13}),
            root=self.root,
            confirmed_inputs={"SOMEONE-ELSE": {0x0F: "Wrong"}},
        ).monitors[0].monitor

        # Since ADR-0014 removed speculative rows, another monitor's confirmed code does not
        # even appear: there is no other source claiming 0x0f exists on this panel.
        self.assertIsNone(monitor.input_for(0x0F))
        self.assertNotIn("Wrong", [i.name for i in monitor.inputs])


class FailedReadTest(DiscoveryTestCase):
    def test_failed_live_read_lists_the_monitor_as_unswitchable(self) -> None:
        """ADR-0013 fails closed; ADR-0011 forbids failing silently."""
        self.one_hp()

        result = discover(
            FakeDdcutil(live_errors={16: NoMonitorOnBus("nothing answered")}),
            root=self.root,
        )

        self.assertEqual(len(result.monitors), 1)
        monitor = result.monitors[0].monitor
        self.assertIsNone(monitor.host_input)
        self.assertFalse(monitor.is_switchable)
        self.assertEqual(result.switchable, ())

    def test_failed_live_read_produces_a_warning(self) -> None:
        self.one_hp()

        result = discover(
            FakeDdcutil(live_errors={16: I2cPermissionDenied("denied")}), root=self.root
        )

        self.assertEqual(len(result.warnings), 1)
        self.assertIn("switching is disabled", result.warnings[0].message)
        self.assertFalse(result.warnings[0].switchable)

    def test_failed_capabilities_read_is_only_a_warning(self) -> None:
        """Capabilities is a hint; losing it must not make the monitor unswitchable."""
        self.one_hp()

        result = discover(
            FakeDdcutil(live={16: 0x13}, capability_errors={16: NoMonitorOnBus("x")}),
            root=self.root,
            include_capabilities=True,
        )

        self.assertTrue(result.monitors[0].monitor.is_switchable)
        self.assertEqual(len(result.warnings), 1)

    def test_one_failed_monitor_does_not_hide_another(self) -> None:
        self.one_hp(bus=16, connector="card1-DP-1")
        make_connector(self.root, "card1-DP-2", bus=17, edid=HP_EDID)

        result = discover(
            FakeDdcutil(live={17: 0x0F}, live_errors={16: NoMonitorOnBus("x")}),
            root=self.root,
        )

        self.assertEqual(len(result.monitors), 2)
        self.assertEqual(len(result.switchable), 1)
        self.assertEqual(result.switchable[0].monitor.bus, 17)


class CapabilitiesCostTest(DiscoveryTestCase):
    def test_capabilities_is_not_consulted_by_default(self) -> None:
        """3.1s is too slow for an interactive path, so it must be opt-in (ADR-0010)."""
        self.one_hp()
        ddcutil = FakeDdcutil(live={16: 0x13}, capabilities={16: REFERENCE_CAPS})

        discover(ddcutil, root=self.root)

        self.assertEqual(ddcutil.capability_calls, [])

    def test_capabilities_is_consulted_when_requested(self) -> None:
        self.one_hp()
        ddcutil = FakeDdcutil(live={16: 0x13}, capabilities={16: REFERENCE_CAPS})

        discover(ddcutil, root=self.root, include_capabilities=True)

        self.assertEqual(ddcutil.capability_calls, [16])


class DetectFallbackTest(DiscoveryTestCase):
    """The NVIDIA path: sysfs publishes no ddc links."""

    def test_falls_back_to_detect(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=None, edid=HP_EDID)
        ddcutil = FakeDdcutil(
            live={16: 0x13},
            detect_result=[
                DetectedDisplay(
                    bus=16,
                    connector="card1-DP-1",
                    manufacturer="HPN",
                    model="HP Z38c",
                    serial="DEMOSERIAL",
                    display_number=1,
                    usable=True,
                )
            ],
        )

        result = discover(ddcutil, root=self.root)

        self.assertTrue(result.used_detect_fallback)
        self.assertEqual(len(result.monitors), 1)
        self.assertEqual(result.monitors[0].key, "DEMOSERIAL")
        self.assertEqual(result.monitors[0].monitor.host_input, 0x13)

    def test_unusable_display_is_warned_not_listed(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=None, edid=HP_EDID)
        ddcutil = FakeDdcutil(
            detect_result=[
                DetectedDisplay(
                    bus=15,
                    connector="card1-eDP-1",
                    manufacturer="LGD",
                    model=None,
                    serial=None,
                    display_number=None,
                    usable=False,
                )
            ]
        )

        result = discover(ddcutil, root=self.root)

        self.assertEqual(result.monitors, ())
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("does not support DDC/CI", result.warnings[0].message)

    def test_detect_failure_is_a_warning_not_a_crash(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=None, edid=HP_EDID)

        result = discover(
            FakeDdcutil(detect_error=I2cPermissionDenied("denied")), root=self.root
        )

        self.assertTrue(result.is_empty)
        self.assertIn("could not list monitors", result.warnings[0].message)

    def test_sysfs_path_does_not_call_detect(self) -> None:
        self.one_hp()
        ddcutil = FakeDdcutil(live={16: 0x13}, detect_error=AssertionError("must not run"))

        result = discover(ddcutil, root=self.root)

        self.assertFalse(result.used_detect_fallback)
        self.assertEqual(len(result.monitors), 1)


class CollisionTest(DiscoveryTestCase):
    def test_colliding_monitors_lose_their_overrides_and_are_warned(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=16, edid=LGD_EDID)
        make_connector(self.root / "card2", "card2-DP-1", bus=22, edid=LGD_EDID)
        ddcutil = FakeDdcutil(live={16: 0x0F, 22: 0x11})

        result = discover(
            ddcutil,
            root=self.root,
            confirmed_inputs={"LGD:product-1826:DP-1": {0x0F: "Should not appear"}},
        )

        # Both still listed and switchable.
        self.assertEqual(len(result.monitors), 2)
        self.assertTrue(all(k.monitor.is_switchable for k in result.monitors))
        # Both flagged.
        self.assertTrue(all(k.collides for k in result.monitors))
        self.assertEqual(len(result.warnings), 2)
        self.assertIn("share the identity", result.warnings[0].message)

    def test_collision_actually_withholds_the_confirmed_map(self) -> None:
        """Not just flagged: the overrides must not reach either monitor (ADR-0013).

        Regression test. Discovery originally applied overrides and detected the collision
        afterwards, so one panel's confirmed map was attributed to another before anything
        noticed -- which is the exact failure the rule exists to prevent.
        """
        make_connector(self.root, "card1-DP-1", bus=16, edid=LGD_EDID)
        make_connector(self.root / "card2", "card2-DP-1", bus=22, edid=LGD_EDID)

        result = discover(
            FakeDdcutil(live={16: 0x0F, 22: 0x11}),
            root=self.root,
            confirmed_inputs={"LGD:product-1826:DP-1": {0x12: "Should not appear"}},
            name_overrides={"LGD:product-1826:DP-1": "Should not appear either"},
        )

        for keyed in result.monitors:
            # Withheld entirely: no row, no name, and nothing at the CONFIRMED tier.
            self.assertIsNone(keyed.monitor.input_for(0x12))
            self.assertNotIn("Should not appear", [i.name for i in keyed.monitor.inputs])
            self.assertNotIn(
                Provenance.CONFIRMED, [i.provenance for i in keyed.monitor.inputs]
            )
            self.assertNotEqual(keyed.monitor.display_name, "Should not appear either")

    def test_name_override_applies_when_there_is_no_collision(self) -> None:
        make_connector(self.root, "card1-DP-1", bus=16, edid=HP_EDID)

        result = discover(
            FakeDdcutil(live={16: 0x13}),
            root=self.root,
            name_overrides={"DEMOSERIAL": "Big Ultrawide"},
        )

        self.assertEqual(result.monitors[0].monitor.display_name, "Big Ultrawide")


class EmptyTest(DiscoveryTestCase):
    def test_no_connectors_yields_nothing(self) -> None:
        result = discover(FakeDdcutil(), root=self.root)

        self.assertTrue(result.is_empty)
        self.assertEqual(result.warnings, ())

    def test_skipped_connectors_are_reported(self) -> None:
        make_connector(self.root, "card1-eDP-1", bus=15, edid=LGD_EDID)

        result = discover(FakeDdcutil(), root=self.root)

        self.assertTrue(result.is_empty)
        self.assertTrue(any("internal panel" in reason for _, reason in result.skipped))


if __name__ == "__main__":
    unittest.main()
