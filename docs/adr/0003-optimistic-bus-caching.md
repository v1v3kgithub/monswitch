# Optimistic I2C Bus Caching with Transparent Fallback

Running `ddcutil detect` takes between 2.5 and 4.0 seconds as it scans every `/dev/i2c-*` bus, causing unacceptable latency when opening launcher interfaces like Ulauncher. In contrast, addressing a known bus directly (e.g. `ddcutil --bus 16 getvcp 60`) completes in ~100ms.

We decided to persist monitor serial-to-bus mappings in `~/.cache/monswitch/bus_cache.json`. When any command runs, it attempts direct communication against the cached bus first. If communication fails or returns an unexpected EDID serial, the system transparently triggers a full `ddcutil detect`, updates the cache file, and retries. A `--refresh` CLI flag is provided to force re-probing manually.

### Considered Options
- **No caching (probed on every call):** Simpler, but introduces multi-second freezes on every launcher invocation.
- **Background udev daemon:** Overengineered; adds complexity and background resource overhead for a tool used intermittently.
- **Optimistic bus cache (Chosen):** Delivers sub-100ms response times without background daemons and self-heals when monitors are replugged.
