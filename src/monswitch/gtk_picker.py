"""GTK3 popup picker (ADR-0008).

Deliberately thin. Filtering, selection and the two-step confirmation all live in
:mod:`monswitch.core.picker`, which is tested without a display; this module owns widgets, key
events and nothing else.

``gi`` is imported lazily inside :func:`run_picker` so that importing this module — which the
CLI does to decide whether a picker is available — cannot fail on a machine without PyGObject.
GTK comes from system ``python3-gi`` and is never a pip dependency (ADR-0012), so a virtualenv
without ``--system-site-packages`` will not find it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .core.config import Config
from .core.ddc import DdcutilError, WriteCapableDdcutil
from .core.models import KeyedMonitor
from .core.picker import ActivationKind, PickerModel, PickerRow
from .core.switching import SwitchRefused, switch_input

__all__ = ["GTK_AVAILABLE", "PickerResult", "gtk_import_error", "run_picker"]

_IMPORT_ERROR: str | None = None

try:  # pragma: no cover - depends on the machine, not on logic
    import gi

    # Both namespaces must be pinned, not just Gtk. On a machine with GTK 3 and GTK 4
    # installed side by side -- which Ubuntu is, by default -- an unversioned
    # `from gi.repository import Gdk` resolves to the newest typelib it can find, loading
    # Gdk 4.0; Gtk 3.0 then requires Gdk 3.0, finds 4.0 already loaded, and the import dies
    # with "Requiring namespace 'Gdk' version '3.0', but '4.0' is already loaded".
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    GTK_AVAILABLE = True
except (ImportError, ValueError) as exc:  # pragma: no cover
    GTK_AVAILABLE = False
    _IMPORT_ERROR = str(exc)


def gtk_import_error() -> str | None:
    """Why GTK is unavailable, or ``None`` if it is available."""
    return _IMPORT_ERROR


@dataclass(frozen=True, slots=True)
class PickerResult:
    """What the picker did, for the caller to report and to pick an exit code from."""

    switched: bool = False
    cancelled: bool = False
    error: str | None = None


CSS = b"""
.monswitch-popup { background-color: @theme_bg_color; }
.monswitch-row-label { font-size: 110%; }
.monswitch-badge { opacity: 0.65; font-size: 90%; }
.monswitch-note { opacity: 0.6; font-size: 85%; }
.monswitch-note-current { opacity: 0.9; font-weight: bold; }
.monswitch-message { padding: 8px; }
.monswitch-close { padding: 0 10px; opacity: 0.6; }
.monswitch-footer { opacity: 0.45; font-size: 85%; padding: 6px; }
"""


def run_picker(
    monitors: Sequence[KeyedMonitor],
    *,
    config: Config,
    ddcutil: WriteCapableDdcutil | None = None,
    message: str | None = None,
) -> PickerResult:
    """Show the picker and act on the user's choice.

    Returns once the window closes. ``message`` is shown in-window rather than on a stderr
    nobody is reading, which is the whole point of ADR-0011's rule that every error reaching a
    graphical front-end is rendered there.
    """
    if not GTK_AVAILABLE:  # pragma: no cover - environment dependent
        return PickerResult(
            error=(f"GTK is not available: {_IMPORT_ERROR}. Install python3-gi and gir1.2-gtk-3.0")
        )

    # Versions were pinned at module import; Gtk first so its own Gdk requirement is
    # already satisfied by the pin rather than by whatever loads first.
    from gi.repository import (
        Gdk,
        Gtk,
    )

    # One Enter switches (ADR-0017). Rows still show provenance, and an unreadable monitor
    # is still refused outright.
    model = PickerModel(monitors, confirm_switch=config.confirm_switch)
    writer = ddcutil or WriteCapableDdcutil()
    outcome = PickerResult(cancelled=True)

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )

    # TOPLEVEL, not POPUP. A POPUP window is override-redirect: the window manager does not
    # manage it and never gives it keyboard focus, so no key events arrive at all -- which
    # took Escape and type-to-filter with it. A decorationless TOPLEVEL with a dialog type
    # hint is what Spotlight-style launchers actually use: frameless as ADR-0008 requires,
    # but focusable.
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_name("monswitch-popup")
    window.get_style_context().add_class("monswitch-popup")
    window.set_decorated(False)
    window.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
    window.set_default_size(520, 380)
    window.set_keep_above(True)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    window.set_modal(True)
    window.set_focus_on_map(True)
    window.set_accept_focus(True)
    window.set_can_focus(True)

    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    window.add(layout)

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    entry = Gtk.SearchEntry()
    entry.set_placeholder_text("Type to filter inputs…")
    header.pack_start(entry, True, True, 0)

    # A visible way out, for anyone reaching for the mouse rather than Escape.
    close_button = Gtk.Button(label="✕")
    close_button.set_relief(Gtk.ReliefStyle.NONE)
    close_button.set_tooltip_text("Close (Esc)")
    close_button.get_style_context().add_class("monswitch-close")
    close_button.set_can_focus(False)
    header.pack_end(close_button, False, False, 0)
    layout.pack_start(header, False, False, 0)

    message_label = Gtk.Label(xalign=0.0)
    message_label.get_style_context().add_class("monswitch-message")
    message_label.set_line_wrap(True)
    layout.pack_start(message_label, False, False, 0)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
    scroller.add(listbox)
    layout.pack_start(scroller, True, True, 0)

    footer = Gtk.Label(label="↑↓ move · Enter switches · Esc close", xalign=0.5)
    footer.get_style_context().add_class("monswitch-footer")
    layout.pack_end(footer, False, False, 0)

    def show_message(text: str | None) -> None:
        if text:
            message_label.set_text(text)
            message_label.show()
        else:
            message_label.hide()

    def build_row(row: PickerRow) -> Gtk.Widget:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.set_margin_top(6)
        line.set_margin_bottom(6)
        line.set_margin_start(10)
        line.set_margin_end(10)

        marker = Gtk.Label(label="●" if row.is_active else "", xalign=0.0)
        marker.set_size_request(14, -1)
        line.pack_start(marker, False, False, 0)

        code = Gtk.Label(label=row.source.hex_code, xalign=0.0)
        code.get_style_context().add_class("monswitch-badge")
        line.pack_start(code, False, False, 0)

        text = Gtk.Label(label=model.display_text(row), xalign=0.0)
        text.get_style_context().add_class("monswitch-row-label")
        line.pack_start(text, False, False, 0)

        # A spacer keeps the note right-aligned.
        line.pack_start(Gtk.Label(label=""), True, True, 0)

        note = row.status_note()
        if note:
            note_label = Gtk.Label(label=note, xalign=1.0)
            style = note_label.get_style_context()
            style.add_class("monswitch-note-current" if row.is_active else "monswitch-note")
            line.pack_end(note_label, False, False, 0)

        container = Gtk.ListBoxRow()
        container.add(line)
        return container

    def refresh() -> None:
        for child in listbox.get_children():
            listbox.remove(child)
        for row, _selected in model.rows_with_state():
            listbox.add(build_row(row))
        listbox.show_all()

        if model.is_empty:
            show_message("Nothing matches that filter.")
        elif not message_label.get_text():
            show_message(None)

        children = listbox.get_children()
        if children and 0 <= model.selected_index < len(children):
            listbox.select_row(children[model.selected_index])

    def perform(row: PickerRow) -> None:
        nonlocal outcome
        try:
            result = switch_input(
                row.monitor,
                row.source,
                ddcutil=writer,
                strategy=_AlreadyConfirmed(),
                confirm_switch=config.confirm_switch,
                # The picker does not confirm (ADR-0017); Enter is the whole interaction.
                assume_yes=True,
                force=True,
            )
        except SwitchRefused as exc:
            show_message(str(exc))
            return
        except DdcutilError as exc:
            show_message(str(exc))
            return

        outcome = PickerResult(switched=result.changed)
        window.close()

    def on_key(_widget: object, event: object) -> bool:
        key = event.keyval  # type: ignore[attr-defined]

        if key == Gdk.KEY_Escape:
            window.close()
            return True

        if key in (Gdk.KEY_Down, Gdk.KEY_Tab):
            model.move(1)
            refresh()
            return True

        if key in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
            model.move(-1)
            refresh()
            return True

        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            activation = model.activate()
            if activation.kind is ActivationKind.SWITCH and activation.row is not None:
                perform(activation.row)
            elif activation.kind is ActivationKind.BLOCKED:
                show_message(activation.message or "Cannot switch that monitor.")
                refresh()
            return True

        return False

    def on_changed(_entry: object) -> None:
        model.set_query(entry.get_text())
        show_message(None)
        refresh()

    # Dismiss-on-focus-loss only once focus has actually been held. Without this guard, a
    # window that never gained focus in the first place closes itself the instant the
    # compositor tells it so, and the picker appears not to open at all.
    had_focus = False

    def on_focus_in(_widget: object, _event: object) -> bool:
        nonlocal had_focus
        had_focus = True
        return False

    def on_focus_out(_widget: object, _event: object) -> bool:
        if had_focus:
            window.close()
        return False

    entry.connect("search-changed", on_changed)
    close_button.connect("clicked", lambda *_: window.close())
    window.connect("key-press-event", on_key)
    # Also on the entry: it has keyboard focus, and connecting here means Escape and the
    # arrow keys still work even if a GTK theme or input method consumes them before the
    # toplevel handler sees them.
    entry.connect("key-press-event", on_key)
    window.connect("focus-in-event", on_focus_in)
    window.connect("focus-out-event", on_focus_out)
    window.connect("destroy", lambda *_: Gtk.main_quit())

    show_message(message)
    refresh()
    window.show_all()
    if not message:
        message_label.hide()

    # present() asks the window manager for focus; show_all() alone does not.
    window.present_with_time(Gdk.CURRENT_TIME)
    entry.grab_focus()
    Gtk.main()

    return outcome


class _AlreadyConfirmed:
    """Strategy for a front-end that does not confirm.

    The picker switches on Enter (ADR-0017), so nothing should ever ask through a strategy. A
    call here would mean a confirmation path was reached that this front-end does not
    implement, so it fails loudly rather than silently approving.
    """

    def choose(self, query: str, choices: Sequence[object]) -> object:
        raise AssertionError("the picker resolves choices itself")

    def confirm(self, prompt: str, *, confirmation: object) -> bool:
        raise AssertionError("the picker confirms via its own two-step Enter")
