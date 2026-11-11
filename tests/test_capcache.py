"""Tests for the capabilities cache.

The cache exists because ADR-0014 left ``capabilities`` as the only source of inputs other
than the live one, while ADR-0010 kept its ~3.1s cost off interactive paths — together making
the launcher list a single row. See ADR-0016.

Most of these tests are about *not* failing. A cache is an optimisation, so every corrupt,
unreadable or unwritable case must behave as a miss: the worst acceptable outcome is paying
for a probe that could have been skipped, and the unacceptable one is discovery breaking
because a cache file is malformed.

Nothing here touches the real cache path; every test uses a tmpdir.

See the note in ``test_edid.py`` on why these are unittest classes.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monswitch.core.capcache import CachedCapabilities, CapabilitiesCache, cache_path

REFERENCE = CachedCapabilities(named={0x0F: "DisplayPort-1", 0x11: "HDMI-1"}, unnamed=(0x1F,))


class CacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.path = self.dir / "capabilities.json"

    def cache(self) -> CapabilitiesCache:
        return CapabilitiesCache(self.path)


class RoundTripTest(CacheTestCase):
    def test_miss_on_an_absent_file(self) -> None:
        self.assertIsNone(self.cache().get("DEMOSERIAL"))

    def test_stores_and_retrieves(self) -> None:
        self.cache().put("DEMOSERIAL", REFERENCE)

        hit = self.cache().get("DEMOSERIAL")

        self.assertEqual(hit, REFERENCE)

    def test_persists_across_instances(self) -> None:
        """The launcher daemon may be killed at any point, so writes land immediately."""
        self.cache().put("DEMOSERIAL", REFERENCE)

        self.assertTrue(self.path.exists())
        self.assertIsNotNone(CapabilitiesCache(self.path).get("DEMOSERIAL"))

    def test_keeps_named_and_unnamed_apart(self) -> None:
        """ADR-0009 forbids naming a code the firmware disclaimed, so the split must survive."""
        self.cache().put("K", REFERENCE)

        hit = self.cache().get("K")

        self.assertEqual(dict(hit.named), {0x0F: "DisplayPort-1", 0x11: "HDMI-1"})
        self.assertEqual(hit.unnamed, (0x1F,))
        self.assertNotIn(0x1F, hit.named)

    def test_several_monitors(self) -> None:
        cache = self.cache()
        cache.put("A", REFERENCE)
        cache.put("B", CachedCapabilities(named={0x12: "HDMI-2"}, unnamed=()))

        self.assertEqual(dict(self.cache().get("B").named), {0x12: "HDMI-2"})
        self.assertEqual(dict(self.cache().get("A").named), dict(REFERENCE.named))

    def test_keys_with_colons_survive(self) -> None:
        """The fallback identity key is mfg:model:connector (ADR-0013)."""
        self.cache().put("HPN:HP Z38c:DP-1", REFERENCE)

        self.assertIsNotNone(self.cache().get("HPN:HP Z38c:DP-1"))

    def test_contains(self) -> None:
        cache = self.cache()
        cache.put("K", REFERENCE)

        self.assertIn("K", cache)
        self.assertNotIn("other", cache)
        self.assertNotIn(42, cache)

    def test_overwrites(self) -> None:
        cache = self.cache()
        cache.put("K", REFERENCE)

        cache.put("K", CachedCapabilities(named={0x13: "USB-C"}, unnamed=()))

        self.assertEqual(dict(self.cache().get("K").named), {0x13: "USB-C"})

    def test_forget_one_entry(self) -> None:
        cache = self.cache()
        cache.put("A", REFERENCE)
        cache.put("B", REFERENCE)

        cache.forget("A")

        self.assertIsNone(self.cache().get("A"))
        self.assertIsNotNone(self.cache().get("B"))

    def test_clear_removes_the_file(self) -> None:
        cache = self.cache()
        cache.put("K", REFERENCE)

        cache.clear()

        self.assertFalse(self.path.exists())
        self.assertIsNone(cache.get("K"))

    def test_creates_the_parent_directory(self) -> None:
        nested = CapabilitiesCache(self.dir / "deep" / "down" / "caps.json")

        nested.put("K", REFERENCE)

        self.assertIsNotNone(CapabilitiesCache(nested.path).get("K"))

    def test_writes_json_a_human_can_read(self) -> None:
        """It lands in a cache directory a user may well inspect when something looks wrong."""
        self.cache().put("K", REFERENCE)

        document = json.loads(self.path.read_text())

        self.assertEqual(document["version"], 1)
        self.assertEqual(document["monitors"]["K"]["named"]["0x0f"], "DisplayPort-1")
        self.assertEqual(document["monitors"]["K"]["unnamed"], ["0x1f"])


class CorruptionTest(CacheTestCase):
    """Every one of these must be a miss, never an exception."""

    def test_invalid_json(self) -> None:
        self.path.write_text("{not json at all")

        self.assertIsNone(self.cache().get("K"))

    def test_wrong_top_level_type(self) -> None:
        self.path.write_text("[1, 2, 3]")

        self.assertIsNone(self.cache().get("K"))

    def test_unknown_version_is_ignored(self) -> None:
        """A future format is not an error; it is simply not understood here."""
        self.path.write_text(json.dumps({"version": 99, "monitors": {"K": {"named": {}}}}))

        self.assertIsNone(self.cache().get("K"))

    def test_monitors_not_a_table(self) -> None:
        self.path.write_text(json.dumps({"version": 1, "monitors": "nope"}))

        self.assertIsNone(self.cache().get("K"))

    def test_entry_not_a_table(self) -> None:
        self.path.write_text(json.dumps({"version": 1, "monitors": {"K": 5}}))

        self.assertIsNone(self.cache().get("K"))

    def test_bad_codes_are_dropped_and_good_ones_kept(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "monitors": {
                        "K": {
                            "named": {"0x0f": "DP", "zz": "junk", "0x999": "nope", "0x11": 7},
                            "unnamed": ["0x1f", "bad", None],
                        }
                    },
                }
            )
        )

        hit = self.cache().get("K")

        self.assertEqual(dict(hit.named), {0x0F: "DP"})
        self.assertEqual(hit.unnamed, (0x1F,))

    def test_entirely_empty_entry_is_a_miss(self) -> None:
        """Nothing worth remembering is indistinguishable from not having asked."""
        self.path.write_text(
            json.dumps({"version": 1, "monitors": {"K": {"named": {}, "unnamed": []}}})
        )

        self.assertIsNone(self.cache().get("K"))

    def test_unreadable_file_is_a_miss(self) -> None:
        self.path.write_text(json.dumps({"version": 1, "monitors": {}}))
        self.path.chmod(0o000)
        self.addCleanup(self.path.chmod, 0o644)

        # Running as root defeats the permission bit, so only assert where it took effect.
        try:
            self.path.read_text()
        except PermissionError:
            self.assertIsNone(self.cache().get("K"))

    def test_unwritable_directory_does_not_raise(self) -> None:
        """An unwritable cache directory must not break discovery."""
        locked = self.dir / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)

        cache = CapabilitiesCache(locked / "caps.json")
        cache.put("K", REFERENCE)  # must not raise

        self.assertIn("K", cache)  # held in memory even though it could not be written

    def test_no_temporary_file_is_left_behind(self) -> None:
        self.cache().put("K", REFERENCE)

        leftovers = list(self.dir.glob("*.tmp"))

        self.assertEqual(leftovers, [])


class CachePathTest(unittest.TestCase):
    def test_honours_xdg_cache_home(self) -> None:
        original = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = "/tmp/xdg-cache-test"
        try:
            self.assertEqual(cache_path(), Path("/tmp/xdg-cache-test/monswitch/capabilities.json"))
        finally:
            if original is None:
                del os.environ["XDG_CACHE_HOME"]
            else:
                os.environ["XDG_CACHE_HOME"] = original

    def test_falls_back_to_dot_cache(self) -> None:
        original = os.environ.pop("XDG_CACHE_HOME", None)
        try:
            self.assertEqual(cache_path(), Path.home() / ".cache/monswitch/capabilities.json")
        finally:
            if original is not None:
                os.environ["XDG_CACHE_HOME"] = original

    def test_is_a_cache_not_config(self) -> None:
        """It is derived data: deleting it must only cost one slow probe, never settings."""
        original = os.environ.pop("XDG_CACHE_HOME", None)
        try:
            self.assertNotIn(".config", str(cache_path()))
        finally:
            if original is not None:
                os.environ["XDG_CACHE_HOME"] = original


if __name__ == "__main__":
    unittest.main()
