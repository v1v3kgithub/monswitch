"""Performing a switch, with the confirmation policy applied.

The single gate in front of the only write in the project. Nothing should call
:meth:`WriteCapableDdcutil.set_input` except :func:`switch_input`, because this is where the
rules from ADR-0002, ADR-0009 and ADR-0013 are enforced together:

1. **No known Host Input, no switch.** A monitor whose current input could not be read is
   refused outright. Without that value there is no way to tell whether the switch would cut
   the display link, so the risk cannot be assessed and the tool does not proceed.
2. **Leaving the Host Input needs agreement**, unless the user has opted out.
3. **An unverified destination needs stronger agreement**, and no opt-out covers it. The code
   may not correspond to a real port on this panel, so the monitor may end up showing nothing
   with no software route back.

Why the two bypasses differ:

``--yes`` (and ``confirm_switch = false``) exist so people can switch quickly between inputs
they use daily. Those are ``live`` or ``confirmed`` by construction, so the bypass is applied
to the *leaving the host* question only. It is not an assertion that a guessed code is safe,
and ADR-0009 is explicit that the opt-out does not extend there. Overriding the unverified
warning therefore takes ``--force``, a separate and deliberately less convenient flag. The
better answer is to confirm the input once — with ``monswitch confirm`` or a config entry —
after which it is ``confirmed`` and ``--yes`` covers it like anything else.

None of this authorises an automated agent to switch anything. An agent needs a human
agreeing in the moment, whatever the flags and config say.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ddc import WriteCapableDdcutil
from .interaction import Declined, InteractionStrategy
from .models import Confirmation, InputSource, Monitor, switch_confirmation

__all__ = [
    "SwitchOutcome",
    "SwitchRefused",
    "switch_input",
]


class SwitchRefused(Exception):
    """The switch was not attempted, and nothing was written.

    Distinct from a write that failed: this means the tool declined before touching the
    monitor. Carries the reason so a front-end can explain it (ADR-0011).
    """

    def __init__(self, message: str, *, confirmation: Confirmation) -> None:
        self.confirmation = confirmation
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SwitchOutcome:
    """What happened.

    ``changed`` is ``False`` for a no-op — asking for the input the monitor is already on —
    which is a success, not a failure, and must not be reported as one.

    ``channel_lost`` records that the switch left the Host Input, so the DDC/CI channel is
    expected to be gone. A front-end should not then try to re-read the monitor and report an
    error when that read fails: losing the channel is what success looks like here.
    """

    monitor: Monitor
    destination: InputSource
    confirmation: Confirmation
    changed: bool
    channel_lost: bool = False


def switch_input(
    monitor: Monitor,
    destination: InputSource,
    *,
    ddcutil: WriteCapableDdcutil,
    strategy: InteractionStrategy,
    confirm_switch: bool = True,
    assume_yes: bool = False,
    force: bool = False,
) -> SwitchOutcome:
    """Switch ``monitor`` to ``destination``, asking first unless told not to.

    Args:
        ddcutil: the write-capable wrapper. Requiring this type means a caller holding a
            read-only :class:`~monswitch.core.ddc.Ddcutil` cannot reach the write path.
        strategy: how to ask the user (ADR-0011). A terminal prompts, a script refuses, a
            picker hands the question back to its own UI.
        confirm_switch: the user's configured preference (ADR-0002).
        assume_yes: skip the *leaving the host* confirmation. Does not cover an unverified
            destination.
        force: additionally skip the unverified-destination confirmation. Prefer confirming
            the input instead; this exists for people who know exactly what they are doing.

    Raises:
        SwitchRefused: if the Host Input is unknown, or the user said no.
        ~monswitch.core.ddc.SetvcpFailed: if the write itself failed.
    """
    verdict = switch_confirmation(monitor, destination, confirm_switch=confirm_switch)

    if verdict is Confirmation.BLOCKED:
        raise SwitchRefused(
            f"{monitor.display_name}: refusing to switch, because its current input could "
            "not be read. Without that, there is no way to tell whether this would cut the "
            "display link. Check that the monitor is on and DDC/CI is enabled in its menu",
            confirmation=verdict,
        )

    # Already on that input. Writing anyway would be harmless but pointless, and reporting
    # "switched" would be a lie.
    if verdict is Confirmation.NONE and destination.code == monitor.host_input:
        return SwitchOutcome(
            monitor=monitor,
            destination=destination,
            confirmation=verdict,
            changed=False,
        )

    if verdict.requires_prompt and not _already_agreed(verdict, assume_yes, force):
        try:
            agreed = strategy.confirm(
                _prompt_for(monitor, destination, verdict), confirmation=verdict
            )
        except Declined as exc:
            raise SwitchRefused(str(exc), confirmation=verdict) from exc
        if not agreed:
            raise SwitchRefused(
                f"{monitor.display_name}: cancelled, nothing was changed",
                confirmation=verdict,
            )

    leaving_host = destination.code != monitor.host_input

    ddcutil.set_input(monitor.bus, destination.code)

    return SwitchOutcome(
        monitor=monitor,
        destination=destination,
        confirmation=verdict,
        changed=True,
        channel_lost=leaving_host,
    )


def _already_agreed(verdict: Confirmation, assume_yes: bool, force: bool) -> bool:
    """Whether a flag already covers this confirmation.

    ``--force`` covers everything. ``--yes`` covers leaving the Host Input but deliberately
    not an unverified destination (ADR-0009).
    """
    if force:
        return True
    if verdict is Confirmation.UNVERIFIED_DESTINATION:
        return False
    return assume_yes


def _prompt_for(monitor: Monitor, destination: InputSource, verdict: Confirmation) -> str:
    """The question put to the user, naming the specific risk."""
    current = monitor.host_input_source
    current_label = current.label if current else f"0x{monitor.host_input:02x}"

    if verdict is Confirmation.UNVERIFIED_DESTINATION:
        return (
            f"{monitor.display_name}: switch to {destination.hex_code} "
            f"{destination.label}?\n"
            f"That input was inferred, not observed, so it may not exist on this monitor. "
            f"If it does not, the screen will go blank and this machine will lose the "
            f"channel it would need to switch back - recovery means the monitor's own "
            f"physical buttons.\n"
            f"This machine is currently on {current_label}."
        )

    return (
        f"{monitor.display_name}: switch from {current_label} to "
        f"{destination.hex_code} {destination.label}? This monitor will stop showing this "
        f"machine, and this machine cannot switch it back"
    )
