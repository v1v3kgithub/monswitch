"""Ulauncher extension for monswitch.

A thin wrapper. Filtering, confirmation and item construction all live in
``monswitch.core.launcher`` and ``monswitch.core.picker``, which are tested without a running
launcher; this file translates between those and Ulauncher's API, and does nothing else. In
particular the two-step confirmation is the picker's, not a second implementation — the one
behaviour where two front-ends drifting apart would be dangerous.

Why the path bootstrap exists (ADR-0012): Ulauncher runs extensions under the *system*
interpreter, so a virtualenv is invisible to it; on an externally-managed distribution
``pip install --user`` is refused by PEP 668; and ``~/.local/lib/pythonX/site-packages`` may
not even exist. So the extension puts monswitch on ``sys.path`` itself, from the
``core_path`` preference or a sibling checkout, and falls back to an already-importable
``monswitch``.

Nothing here runs discovery at import time. The daemon imports every extension at startup, and
a DDC/CI read costs 0.35s per monitor — paying that on launcher startup, for users who never
type the keyword, is exactly the stall ADR-0012 forbids.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_import_path() -> str | None:
    """Put monswitch on ``sys.path``. Returns an error message, or ``None`` on success."""
    candidates: list[Path] = []

    configured = os.environ.get("MONSWITCH_SRC")
    if configured:
        candidates.append(Path(configured).expanduser())

    # This file sits at the repository root, because that is where Ulauncher's installer
    # expects manifest.json and main.py when it clones an extension from a URL. The core is
    # therefore in ./src of the very clone Ulauncher made, and nothing needs configuring.
    # The parent is kept as a fallback for a layout that nests the extension a level down.
    here = Path(__file__).resolve().parent
    candidates.extend([here / "src", here.parent / "src"])

    for candidate in candidates:
        if (candidate / "monswitch" / "__init__.py").exists():
            resolved = str(candidate)
            if resolved not in sys.path:
                sys.path.insert(0, resolved)
            return None

    try:
        import monswitch  # noqa: F401 - probing importability
    except ImportError:
        return (
            "monswitch could not be imported. Set the 'monswitch source path' preference "
            "to the src/ directory of your checkout, or export MONSWITCH_SRC."
        )
    return None


BOOTSTRAP_ERROR = _bootstrap_import_path()

from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.client.Extension import Extension
from ulauncher.api.shared.action.ExtensionCustomAction import (
    ExtensionCustomAction,
)
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.action.RenderResultListAction import (
    RenderResultListAction,
)
from ulauncher.api.shared.event import ItemEnterEvent, KeywordQueryEvent
from ulauncher.api.shared.item.ExtensionResultItem import (
    ExtensionResultItem,
)

if BOOTSTRAP_ERROR is None:
    from monswitch.core.capcache import CapabilitiesCache
    from monswitch.core.config import Config, load_tolerant
    from monswitch.core.ddc import DdcutilError, WriteCapableDdcutil
    from monswitch.core.discovery import discover
    from monswitch.core.launcher import (
        ActionKind,
        DiscoverySnapshot,
        ItemAction,
        LauncherItem,
        LauncherSession,
    )
    from monswitch.core.picker import ActivationKind
    from monswitch.core.switching import SwitchRefused, switch_input

ICON = "images/icon.svg"


class MonswitchExtension(Extension):
    def __init__(self) -> None:
        super().__init__()
        self.subscribe(KeywordQueryEvent, QueryListener())
        self.subscribe(ItemEnterEvent, EnterListener())
        self._snapshot: object | None = None
        self._session: object | None = None

    # -- lazily built, never at import or construction time --------------

    def config(self) -> Config:
        """Read config only if the user named a file, matching the CLI's opt-in rule."""
        path = (self.preferences.get("config_path") or "").strip()
        if not path:
            return Config()
        config, _warnings = load_tolerant(Path(path).expanduser())
        return config

    def snapshot(self) -> DiscoverySnapshot:
        if self._snapshot is None:
            config = self.config()

            cache = CapabilitiesCache()

            def run() -> object:
                # capabilities IS consulted here, through the cache. ADR-0014 removed the
                # built-in code table, which left capabilities as the only source of inputs
                # other than the live one -- so without this the launcher lists a single row,
                # the input you are already on, which is no list at all. The cache makes it
                # affordable: ~3.1s the first time a monitor is seen, free thereafter, since
                # capabilities describes firmware that does not change (ADR-0016).
                return discover(
                    include_capabilities=True,
                    confirmed_inputs=config.confirmed_inputs(),
                    name_overrides=config.name_overrides(),
                    capabilities_cache=cache,
                )

            self._snapshot = DiscoverySnapshot(discover=run)
        return self._snapshot  # type: ignore[return-value]

    def session(self):
        """The session holding the model between events.

        All model handling lives in the core, because a launcher's query and activation paths
        must reach it differently and getting that wrong is invisible: routing Enter through
        the query path disarms the very row it is meant to confirm, so nothing switches.
        """
        if self._session is None:
            self._session = LauncherSession(
                snapshot=self.snapshot(),
                confirm_switch=self.config().confirm_switch,
                # One Enter switches, at the user's explicit request (ADR-0017).
                require_confirmation=False,
            )
        return self._session


def _render(items: tuple[LauncherItem, ...]) -> RenderResultListAction:
    rendered = []
    for item in items:
        action = (
            ExtensionCustomAction(item.action.as_data(), keep_app_open=True)
            if item.selectable
            else HideWindowAction()
        )
        rendered.append(
            ExtensionResultItem(
                icon=ICON,
                name=item.title,
                description=item.subtitle,
                on_enter=action,
            )
        )
    return RenderResultListAction(rendered)


def _error_item(message: str) -> RenderResultListAction:
    """Render a failure in the launcher itself.

    ADR-0011: a graphical front-end never exits silently. There is no terminal here, so an
    exception that only reaches the daemon's log is invisible to the person waiting.
    """
    return RenderResultListAction(
        [
            ExtensionResultItem(
                icon=ICON,
                name="monswitch: cannot list inputs",
                description=message,
                on_enter=HideWindowAction(),
            )
        ]
    )


class QueryListener(EventListener):
    def on_event(self, event, extension):
        if BOOTSTRAP_ERROR is not None:
            return _error_item(BOOTSTRAP_ERROR)

        query = (event.get_argument() or "").strip()
        try:
            items = extension.session().items_for(query)
        except DdcutilError as exc:
            return _error_item(str(exc))
        except Exception as exc:
            return _error_item(f"{type(exc).__name__}: {exc}")

        return _render(items)


class EnterListener(EventListener):
    def on_event(self, event, extension):
        if BOOTSTRAP_ERROR is not None:
            return _error_item(BOOTSTRAP_ERROR)

        action = ItemAction.from_data(event.get_data())
        if action.kind is ActionKind.NOTHING:
            return HideWindowAction()

        session = extension.session()
        kind, row = session.activate(action)

        if row is None or kind is ActivationKind.NOTHING:
            return _error_item("that input is no longer available; try again")

        if kind is ActivationKind.BLOCKED:
            return _error_item(
                f"{row.monitor.display_name}: its current input could not be read, "
                "so switching is disabled"
            )

        if kind is ActivationKind.ARM:
            # Re-render with the row asking for confirmation, keeping the window open and the
            # query untouched -- re-querying here would disarm it immediately.
            return _render(session.rerender())

        config = extension.config()
        try:
            switch_input(
                row.monitor,
                row.source,
                ddcutil=WriteCapableDdcutil(),
                strategy=_AlreadyConfirmed(),
                confirm_switch=config.confirm_switch,
                # The second Enter was the confirmation, given against the specific warning.
                assume_yes=True,
                force=True,
            )
        except (SwitchRefused, DdcutilError) as exc:
            return _error_item(str(exc))

        session.invalidate()
        return HideWindowAction()


class _AlreadyConfirmed:
    """The launcher's second Enter is the confirmation, so nothing may ask again.

    Fails loudly rather than approving, because any call here would mean the arming step was
    bypassed.
    """

    def choose(self, query, choices):
        raise AssertionError("the launcher resolves choices itself")

    def confirm(self, prompt, *, confirmation):
        raise AssertionError("the launcher confirms via its own two-step Enter")


if __name__ == "__main__":
    MonswitchExtension().run()
