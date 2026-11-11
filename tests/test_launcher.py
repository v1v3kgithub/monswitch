"""Tests for the launcher-facing logic.

No Ulauncher daemon, and no import of ``ulauncher`` — these cover the parts the extension
delegates to, which is deliberately almost all of it.

Two behaviours carry the weight. Actions round-trip by bus and code rather than by row
position, because a launcher sends them back through its own event system and the list may
have changed meanwhile; resolving by index would switch an input the user never chose. And a
stale snapshot forces confirmation on, so cached data cannot combine with an opt-out to switch
away from the host unwarned.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from monswitch.core.discovery import Discovery
from monswitch.core.edid import EdidIdentity
from monswitch.core.launcher import (
    ActionKind,
    DiscoverySnapshot,
    ItemAction,
    build_items,
    resolve_action,
)
from monswitch.core.models import InputSource, KeyedMonitor, Monitor, Provenance
from monswitch.core.picker import ActivationKind, PickerModel

HP = EdidIdentity(manufacturer="HPN", product_code=13327, model="HP Z38c", serial="DEMOSERIAL")
DELL = EdidIdentity(manufacturer="DEL", product_code=11, model="Dell U24", serial="DELL1")

USBC = InputSource(code=0x13, name="USB-C", provenance=Provenance.LIVE)
DESKTOP = InputSource(code=0x0F, name="Desktop PC", provenance=Provenance.CONFIRMED)
HDMI = InputSource(code=0x11, name="HDMI-1", provenance=Provenance.REPORTED)


def keyed(
    identity: EdidIdentity = HP,
    *inputs: InputSource,
    bus: int = 16,
    host_input: int | None = 0x13,
    key: str = "DEMOSERIAL",
) -> KeyedMonitor:
    monitor = Monitor(
        identity=identity,
        bus=bus,
        connector="DP-1",
        inputs=inputs or (USBC, DESKTOP, HDMI),
        host_input=host_input,
    )
    return KeyedMonitor(monitor=monitor, key=key)


def model(*monitors: KeyedMonitor, confirm_switch: bool = True) -> PickerModel:
    return PickerModel(monitors or (keyed(),), confirm_switch=confirm_switch)


class ItemBuildingTest(unittest.TestCase):
    def test_one_item_per_input(self) -> None:
        items = build_items(model())

        self.assertEqual(len(items), 3)

    def test_active_input_switches_without_arming(self) -> None:
        items = build_items(model())

        usbc = next(i for i in items if i.action.code == 0x13)

        self.assertEqual(usbc.action.kind, ActionKind.SWITCH)
        self.assertIn("current input", usbc.subtitle)

    def test_confirmed_input_arms_first(self) -> None:
        items = build_items(model())

        desktop = next(i for i in items if i.action.code == 0x0F)

        self.assertEqual(desktop.action.kind, ActionKind.ARM)

    def test_unverified_input_is_labelled(self) -> None:
        items = build_items(model())

        hdmi = next(i for i in items if i.action.code == 0x11)

        self.assertEqual(hdmi.action.kind, ActionKind.ARM)
        self.assertIn("unverified", hdmi.subtitle)

    def test_subtitle_names_the_monitor(self) -> None:
        items = build_items(model())

        self.assertTrue(all("HP Z38c" in i.subtitle for i in items))

    def test_action_carries_bus_and_code_not_position(self) -> None:
        items = build_items(model())

        for item in items:
            with self.subTest(title=item.title):
                self.assertEqual(item.action.bus, 16)
                self.assertIsNotNone(item.action.code)

    def test_blocked_monitor_items_are_blocked(self) -> None:
        items = build_items(model(keyed(HP, USBC, DESKTOP, host_input=None)))

        self.assertTrue(all(i.action.kind is ActionKind.BLOCKED for i in items))
        self.assertIn("cannot switch", items[0].subtitle)

    def test_badges_appear_with_several_monitors(self) -> None:
        items = build_items(model(keyed(), keyed(DELL, USBC, HDMI, bus=17, key="DELL1")))

        self.assertEqual(len(items), 5)
        self.assertTrue(any(i.title.startswith("[") for i in items))

    def test_empty_result_is_a_single_unselectable_row(self) -> None:
        subject = model()
        subject.set_query("zzzz")

        items = build_items(subject)

        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].selectable)
        self.assertEqual(items[0].action.kind, ActionKind.NOTHING)

    def test_empty_message_can_be_supplied(self) -> None:
        subject = model()
        subject.set_query("zzzz")

        items = build_items(subject, empty_message="No monitor found")

        self.assertEqual(items[0].title, "No monitor found")

    def test_stale_snapshot_is_surfaced(self) -> None:
        """The user is about to act on this data, so say it is unconfirmed."""
        items = build_items(model(), snapshot_stale=True)

        self.assertTrue(any("checking" in i.subtitle for i in items))

    def test_armed_row_shows_the_action_first(self) -> None:
        subject = model()
        subject.select(1)
        subject.activate()

        items = build_items(subject)
        armed = next(i for i in items if i.action.code == 0x0F)

        self.assertTrue(armed.is_warning)
        self.assertTrue(armed.subtitle.startswith("Press Enter again to switch"))
        self.assertEqual(armed.action.kind, ActionKind.SWITCH)

    def test_armed_row_keeps_its_name_visible(self) -> None:
        subject = model()
        subject.select(1)
        subject.activate()

        armed = next(i for i in build_items(subject) if i.action.code == 0x0F)

        self.assertIn("Desktop PC", armed.title)


class ActionRoundTripTest(unittest.TestCase):
    def test_serialises_and_rebuilds(self) -> None:
        action = ItemAction(kind=ActionKind.ARM, bus=16, code=0x0F)

        self.assertEqual(ItemAction.from_data(action.as_data()), action)

    def test_malformed_data_becomes_a_no_op(self) -> None:
        """This crosses a process boundary; the safe failure is to do nothing."""
        for data in (None, "nonsense", {}, {"kind": "explode"}, {"kind": None}, 42):
            with self.subTest(data=data):
                self.assertEqual(ItemAction.from_data(data).kind, ActionKind.NOTHING)

    def test_non_integer_bus_and_code_are_dropped(self) -> None:
        action = ItemAction.from_data({"kind": "switch", "bus": "16", "code": "0x0f"})

        self.assertIsNone(action.bus)
        self.assertIsNone(action.code)


class ResolveActionTest(unittest.TestCase):
    def test_resolves_by_bus_and_code(self) -> None:
        subject = model()

        kind, row = resolve_action(subject, ItemAction(kind=ActionKind.ARM, bus=16, code=0x0F))

        self.assertEqual(kind, ActivationKind.ARM)
        self.assertIsNotNone(row)
        self.assertEqual(row.source.code, 0x0F)

    def test_resolution_is_independent_of_row_order(self) -> None:
        """The list may have been re-filtered between render and Enter."""
        subject = model()
        subject.set_query("desktop")

        kind, row = resolve_action(subject, ItemAction(kind=ActionKind.ARM, bus=16, code=0x0F))

        self.assertEqual(row.source.code, 0x0F)
        self.assertEqual(kind, ActivationKind.ARM)

    def test_second_activation_switches(self) -> None:
        subject = model()
        action = ItemAction(kind=ActionKind.ARM, bus=16, code=0x0F)

        resolve_action(subject, action)
        kind, _row = resolve_action(subject, action)

        self.assertEqual(kind, ActivationKind.SWITCH)

    def test_vanished_row_does_nothing(self) -> None:
        """A monitor unplugged between render and Enter must not switch a near match."""
        subject = model()

        kind, row = resolve_action(subject, ItemAction(kind=ActionKind.SWITCH, bus=99, code=0x0F))

        self.assertEqual(kind, ActivationKind.NOTHING)
        self.assertIsNone(row)

    def test_wrong_code_on_the_right_bus_does_nothing(self) -> None:
        subject = model()

        kind, _row = resolve_action(subject, ItemAction(kind=ActionKind.SWITCH, bus=16, code=0xAA))

        self.assertEqual(kind, ActivationKind.NOTHING)

    def test_incomplete_action_does_nothing(self) -> None:
        kind, row = resolve_action(model(), ItemAction(kind=ActionKind.SWITCH))

        self.assertEqual(kind, ActivationKind.NOTHING)
        self.assertIsNone(row)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.calls = 0

    def discovery(self) -> Discovery:
        self.calls += 1
        return Discovery(monitors=(keyed(),))

    def snapshot(self, ttl: float = 10.0) -> DiscoverySnapshot:
        return DiscoverySnapshot(discover=self.discovery, ttl=ttl, clock=self.clock)

    def test_nothing_runs_until_first_use(self) -> None:
        """ADR-0012: no discovery at import or construction; the daemon must start fast."""
        self.snapshot()

        self.assertEqual(self.calls, 0)

    def test_first_get_fetches(self) -> None:
        snapshot = self.snapshot()

        snapshot.get()

        self.assertEqual(self.calls, 1)

    def test_repeated_gets_within_ttl_share_one_read(self) -> None:
        """Keystrokes in one launcher session must not each cost a DDC/CI round trip."""
        snapshot = self.snapshot()

        for _ in range(5):
            snapshot.get()
        self.clock.advance(5)
        snapshot.get()

        self.assertEqual(self.calls, 1)

    def test_refreshes_after_ttl(self) -> None:
        snapshot = self.snapshot(ttl=10.0)
        snapshot.get()

        self.clock.advance(11)
        snapshot.get()

        self.assertEqual(self.calls, 2)

    def test_invalidate_forces_a_refresh(self) -> None:
        snapshot = self.snapshot()
        snapshot.get()

        snapshot.invalidate()
        snapshot.get()

        self.assertEqual(self.calls, 2)

    def test_is_stale_before_any_fetch(self) -> None:
        self.assertTrue(self.snapshot().is_stale)

    def test_not_stale_immediately_after_fetch(self) -> None:
        snapshot = self.snapshot()
        snapshot.get()

        self.assertFalse(snapshot.is_stale)

    def test_failed_refresh_keeps_the_previous_result(self) -> None:
        """A probe failure must not empty the list; stale-but-labelled beats nothing."""
        failing = DiscoverySnapshot(discover=self.discovery, ttl=10.0, clock=self.clock)
        failing.get()
        self.clock.advance(11)

        def boom() -> Discovery:
            raise RuntimeError("ddcutil exploded")

        failing.discover = boom
        result = failing.get()

        self.assertEqual(len(result.monitors), 1)

    def test_first_failure_propagates(self) -> None:
        """With nothing cached there is nothing to serve, so the caller must hear about it."""

        def boom() -> Discovery:
            raise RuntimeError("ddcutil exploded")

        snapshot = DiscoverySnapshot(discover=boom, clock=self.clock)

        with self.assertRaises(RuntimeError):
            snapshot.get()

    def test_unrefreshable_stale_data_forces_confirmation_on(self) -> None:
        """ADR-0012: an opt-out must not combine with unconfirmed data to switch unwarned.

        The case is a refresh that *failed*, so the previous result is still being served.
        Data that was stale and refreshed successfully is fresh, and is trusted -- an earlier
        version of this test confused the two.
        """
        snapshot = self.snapshot(ttl=10.0)
        snapshot.get()
        self.clock.advance(11)

        def boom() -> Discovery:
            raise RuntimeError("ddcutil exploded")

        snapshot.discover = boom

        built = snapshot.model(confirm_switch=False)
        built.select(1)

        self.assertTrue(snapshot.is_stale)
        self.assertEqual(built.activate().kind, ActivationKind.ARM)

    def test_successfully_refreshed_data_is_trusted(self) -> None:
        snapshot = self.snapshot(ttl=10.0)
        snapshot.get()
        self.clock.advance(11)

        built = snapshot.model(confirm_switch=False)
        built.select(1)

        self.assertFalse(snapshot.is_stale)
        self.assertEqual(built.activate().kind, ActivationKind.SWITCH)

    def test_fresh_snapshot_honours_the_preference(self) -> None:
        snapshot = self.snapshot()

        built = snapshot.model(confirm_switch=False)
        built.select(1)

        self.assertEqual(built.activate().kind, ActivationKind.SWITCH)


class ExtensionShapeTest(unittest.TestCase):
    """Guards on the extension file itself, which the logic tests cannot reach.

    The extension cannot be exercised here -- it needs a running Ulauncher daemon -- so these
    check the things that silently break it: the manifest's API version, and the rule that
    nothing probes hardware at import time (the daemon imports every extension at startup,
    and a DDC/CI read costs 0.35s per monitor).
    """

    @property
    def extension_dir(self) -> Path:
        return Path(__file__).parent.parent / "extension"

    def test_manifest_declares_api_v2(self) -> None:
        manifest = json.loads((self.extension_dir / "manifest.json").read_text())

        self.assertEqual(manifest["required_api_version"], "^2.0.0")

    def test_manifest_has_a_keyword_preference(self) -> None:
        manifest = json.loads((self.extension_dir / "manifest.json").read_text())
        kinds = [p["type"] for p in manifest["preferences"]]

        self.assertIn("keyword", kinds)

    def test_manifest_icon_exists(self) -> None:
        manifest = json.loads((self.extension_dir / "manifest.json").read_text())

        self.assertTrue((self.extension_dir / manifest["icon"]).exists())

    def test_main_parses(self) -> None:
        ast.parse((self.extension_dir / "main.py").read_text())

    def test_no_discovery_at_import_time(self) -> None:
        """ADR-0012: a module-level probe would stall Ulauncher's startup for every user."""
        tree = ast.parse((self.extension_dir / "main.py").read_text())

        toplevel_calls = [
            node
            for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        ]
        called = {
            node.value.func.id for node in toplevel_calls if isinstance(node.value.func, ast.Name)
        }

        self.assertNotIn("discover", called)
        self.assertNotIn("Ddcutil", called)

    def test_bootstrap_runs_before_the_core_is_imported(self) -> None:
        """The path wiring is the whole point; importing first would defeat it."""
        text = (self.extension_dir / "main.py").read_text()

        self.assertLess(
            text.index("BOOTSTRAP_ERROR = _bootstrap_import_path()"),
            text.index("from monswitch.core"),
        )

    def test_extension_delegates_rather_than_reimplementing(self) -> None:
        """Confirmation must come from the shared picker, not a second copy here."""
        text = (self.extension_dir / "main.py").read_text()

        self.assertIn("from monswitch.core.launcher import", text)
        self.assertIn("resolve_action", text)
        self.assertNotIn("def _warning_for", text)


if __name__ == "__main__":
    unittest.main()
