"""The VisionCore AI action allowlist and its route into the safety system.

An AI reply may carry at most one action, and this module is the only place
where such a request is turned into something that can move the machine. It
never calls a controller, a backend or the operating system directly: it maps an
allowlisted action onto an :class:`~app.interaction.intent.Intent` and hands that
intent to the *existing* :class:`~app.interaction.intent.IntentRouter`, which is
the same router the HUD buttons and the gesture pipeline use.

The chain, in order (Phase 7, section 11)::

    parser -> schema validation -> allowlist -> mode check
           -> control-enabled check -> emergency check -> capability check
           -> existing controller -> platform

Nothing outside the table below can run. A request for a shell command, a
shutdown, a restart, a logout, a file operation or any other system change is
not merely rejected by the router - it is not representable at all: the action
vocabulary is closed and every entry ends in a controller that already existed
before Phase 7.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from app.ai.context import AIContext
from app.ai.types import AIActionKind, AIActionOutcome, AIActionPlan
from app.controls.device_types import DeviceAction
from app.controls.safety import ControlMode
from app.interaction.intent import Intent, IntentKind, IntentOutcome, IntentSource

logger = logging.getLogger("visioncore.ai.router")

# Sentence required for anything the assistant must never implement.
UNAVAILABLE_SENTENCE = "That action isn't available through VisionCore AI."

# Provider spellings accepted for the two control modes.
_MODE_ALIASES = {"MOUSE": ControlMode.MOUSE, "DEVICE": ControlMode.DEVICE}

# Kind spellings accepted, including the "MOUSE_ACTION" form from the brief.
_KIND_ALIASES = {
    "DEVICE_ACTION": AIActionKind.DEVICE_ACTION,
    "CONTROL_MODE": AIActionKind.CONTROL_MODE,
    "CONTROL_ACTION": AIActionKind.CONTROL_ACTION,
    "MOUSE_ACTION": AIActionKind.CONTROL_ACTION,
}

# Action spellings accepted per kind (canonical name -> canonical name).
_ACTION_ALIASES = {
    AIActionKind.CONTROL_ACTION: {
        "PAUSE": "PAUSE_CONTROL",
        "RESUME": "RESUME_CONTROL",
        "DISABLE": "DISABLE_CONTROL",
        "MOUSE_MODE": "MOUSE_MODE",
        "DEVICE_MODE": "DEVICE_MODE",
    },
    AIActionKind.CONTROL_MODE: {
        "MOUSE_MODE": "MOUSE",
        "DEVICE_MODE": "DEVICE",
    },
    AIActionKind.DEVICE_ACTION: {},
}

# System level requests that must not be implementable in any form.
BLOCKED_ACTION_NAMES = frozenset(
    {
        "SHUTDOWN", "SHUT_DOWN", "POWER_OFF", "REBOOT", "RESTART", "LOGOUT",
        "LOG_OUT", "SIGN_OUT", "SLEEP", "HIBERNATE", "SUSPEND",
        "RUN_COMMAND", "RUN", "EXECUTE", "EXEC", "SHELL", "TERMINAL", "CMD",
        "COMMAND", "POWERSHELL", "BASH", "SUDO", "KILL", "TASKKILL",
        "DELETE", "DELETE_FILE", "REMOVE", "FORMAT", "INSTALL", "UNINSTALL",
        "WRITE_FILE", "READ_FILE", "CREATE_FILE", "MOVE_FILE", "OPEN_URL",
        "DOWNLOAD", "UPLOAD", "SEND_EMAIL", "SCREENSHOT", "RECORD",
    }
)


@dataclass(frozen=True, slots=True)
class AIActionSpec:
    """One entry of the closed action table."""

    kind: AIActionKind
    action: str
    label: str
    intent: IntentKind
    device_action: Optional[DeviceAction] = None
    requires_confirmation: bool = False
    summary: str = ""

    @property
    def signature(self) -> str:
        return f"{self.kind.value}:{self.action}"


def _device(
    action: DeviceAction,
    requires_confirmation: bool = False,
) -> AIActionSpec:
    return AIActionSpec(
        kind=AIActionKind.DEVICE_ACTION,
        action=action.value,
        label=action.label,
        intent=IntentKind.DEVICE_ACTION,
        device_action=action,
        requires_confirmation=requires_confirmation,
    )


# The allowlist: every action VisionCore can perform today, and nothing else.
ACTION_TABLE: Dict[Tuple[AIActionKind, str], AIActionSpec] = {
    spec.signature: spec
    for spec in (
        _device(DeviceAction.VOLUME_UP),
        _device(DeviceAction.VOLUME_DOWN),
        _device(DeviceAction.MUTE),
        _device(DeviceAction.PLAY_PAUSE),
        _device(DeviceAction.NEXT_TRACK),
        _device(DeviceAction.PREVIOUS_TRACK),
        _device(DeviceAction.BRIGHTNESS_UP),
        _device(DeviceAction.BRIGHTNESS_DOWN),
        # Window operations change what the user is looking at, so they ask first.
        _device(DeviceAction.MINIMIZE, requires_confirmation=True),
        _device(DeviceAction.MAXIMIZE, requires_confirmation=True),
        _device(DeviceAction.NEXT_WINDOW, requires_confirmation=True),
        AIActionSpec(
            kind=AIActionKind.CONTROL_MODE,
            action=ControlMode.MOUSE.value,
            label="MOUSE MODE",
            intent=IntentKind.MODE_MOUSE,
            summary="hand the pointer back to the mouse control layer",
        ),
        AIActionSpec(
            kind=AIActionKind.CONTROL_MODE,
            action=ControlMode.DEVICE.value,
            label="DEVICE MODE",
            intent=IntentKind.MODE_DEVICE,
            summary="send gestures to the device control layer",
        ),
        AIActionSpec(
            kind=AIActionKind.CONTROL_ACTION,
            action="PAUSE_CONTROL",
            label="PAUSE CONTROL",
            intent=IntentKind.CONTROL_TOGGLE,
            summary="pause the pointer without disabling control",
        ),
        AIActionSpec(
            kind=AIActionKind.CONTROL_ACTION,
            action="RESUME_CONTROL",
            label="RESUME CONTROL",
            intent=IntentKind.SAFETY_RESET,
            summary="resume control through the existing safety reset path",
        ),
        AIActionSpec(
            kind=AIActionKind.CONTROL_ACTION,
            action="DISABLE_CONTROL",
            label="DISABLE CONTROL",
            intent=IntentKind.CONTROL_DISABLE,
            requires_confirmation=True,
            summary="disarm control and release everything it holds",
        ),
    )
}

ACTION_NAMES: Tuple[str, ...] = tuple(sorted({spec.action for spec in ACTION_TABLE.values()}))


# --------------------------------------------------------------------------- #
# Allowlist resolution
# --------------------------------------------------------------------------- #


def resolve(kind: AIActionKind, action: str) -> Optional[AIActionSpec]:
    """Look an action up in the closed table (None when it is not allowed)."""
    name = (action or "").strip().upper().replace(" ", "_").replace("-", "_")
    if not name:
        return None
    name = _ACTION_ALIASES.get(kind, {}).get(name, name)
    return ACTION_TABLE.get(f"{kind.value}:{name}")


def resolve_kind(raw: object) -> Optional[AIActionKind]:
    """Map the requested type onto a known family, or refuse it."""
    return _KIND_ALIASES.get(str(raw or "").strip().upper())


def is_blocked(name: object) -> bool:
    """True for system level requests the assistant must never carry out."""
    return str(name or "").strip().upper().replace(" ", "_") in BLOCKED_ACTION_NAMES


def normalise(plan: AIActionPlan) -> Tuple[Optional[AIActionSpec], str]:
    """Validate a plan against the allowlist.

    Returns the matching spec, or ``None`` plus the reason it was refused. The
    caller never sees an action that is not in :data:`ACTION_TABLE`.
    """
    if is_blocked(plan.action):
        return None, "BLOCKED REQUEST"
    spec = resolve(plan.kind, plan.action)
    if spec is None:
        return None, "UNSUPPORTED ACTION"
    return spec, ""


def mode_for(action: str) -> Optional[ControlMode]:
    """Resolve a requested control mode (used by the parser)."""
    return _MODE_ALIASES.get((action or "").strip().upper())


# Requests that describe a system change VisionCore deliberately cannot make.
_SYSTEM_REQUEST_PATTERNS = (
    re.compile(r"\b(shut\s?down|power\s?(off|down)|reboot|restart|log\s?out|sign\s?out)\b", re.I),
    re.compile(r"\b(hibernate|sleep\s+the\s+(pc|computer|system|machine))\b", re.I),
    re.compile(r"\b(format|wipe)\b[^.]{0,24}\b(disk|drive|computer|pc|system)\b", re.I),
    re.compile(
        r"\b(run|execute|open|start)\b[^.]{0,24}\b(command|terminal|shell|powershell|cmd|"
        r"bash|script|console)\b",
        re.I,
    ),
    re.compile(r"\b(sudo|rm\s+-rf|chmod|regedit|taskkill|killall|schtasks|net\s+user)\b", re.I),
    re.compile(r"\b(install|uninstall|download|upload|patch)\b[^.]{0,24}\b(software|program|"
               r"app|driver|package|file|update)\b", re.I),
    re.compile(r"\b(delete|erase|remove)\b[^.]{0,24}\b(file|files|folder|directory|everything|"
               r"account|disk)\b", re.I),
    re.compile(r"\b(open|visit|browse|go\s+to)\b[^.]{0,16}\b(url|website|browser|link|internet)\b", re.I),
    re.compile(r"\b(send|draft|write)\b[^.]{0,16}\b(email|message)\b", re.I),
)


def requests_system_change(text: str) -> bool:
    """True when a user message asks for something VisionCore cannot do.

    Checked before any provider call, so the fixed refusal does not depend on a
    model behaving well, and so no request of this kind ever reaches the network.
    """
    clean = " ".join((text or "").lower().split())
    return any(pattern.search(clean) for pattern in _SYSTEM_REQUEST_PATTERNS)


def describe_actions() -> str:
    """One-line capability summary used by the local answerer."""
    return ", ".join(spec.label for spec in ACTION_TABLE.values())


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


class ActionRouter:
    """Validates an AI plan and routes it through the existing intent router."""

    def __init__(
        self,
        dispatch: Callable[[Intent], IntentOutcome],
        telemetry: Optional[Callable[[], object]] = None,
    ) -> None:
        self._dispatch = dispatch
        self._telemetry = telemetry
        self.considered = 0
        self.refused = 0
        self.executed = 0

    # -- public ------------------------------------------------------------ #

    def route(self, plan: AIActionPlan, context: AIContext) -> AIActionOutcome:
        """Run the gate chain for one plan and report what really happened."""
        self.considered += 1

        spec, reason = normalise(plan)
        if spec is None:
            self.refused += 1
            logger.info("AI action %s refused: %s", plan.signature, reason)
            return AIActionOutcome(plan, message=reason, reason=reason)

        grounded = AIActionPlan(
            kind=spec.kind,
            action=spec.action,
            label=spec.label,
            requires_confirmation=spec.requires_confirmation,
        )

        reason = self._precheck(spec, context)
        if reason:
            self.refused += 1
            logger.info("AI action %s refused: %s", spec.signature, reason)
            return AIActionOutcome(grounded, message=reason, reason=reason)

        intent = Intent.create(
            spec.intent,
            IntentSource.AI,
            label=spec.label,
            payload=spec.action,
        )
        outcome = self._dispatch(intent)
        return self._report(grounded, outcome)

    # -- gate chain -------------------------------------------------------- #

    @staticmethod
    def _precheck(spec: AIActionSpec, context: AIContext) -> str:
        """Mode, control, emergency and capability gates, in that order."""
        # 1. Mode check: a control-layer action may only address the layer that
        #    is actually selectable, and a mode change may only name a mode.
        mode_reason = ActionRouter._mode_reason(spec, context)
        if mode_reason:
            return mode_reason

        # 2. Control-enabled check: the owning layer must be armed for work.
        enabled_reason = ActionRouter._enabled_reason(spec, context)
        if enabled_reason:
            return enabled_reason

        # 3. Emergency check: an active emergency stop outranks everything the
        #    assistant can ask for. Only the human recovery path clears it.
        if context.emergency:
            return "EMERGENCY STOP ACTIVE"

        # 4. Capability check: the platform must really offer the feature.
        return ActionRouter._capability_reason(spec, context)

    @staticmethod
    def _mode_reason(spec: AIActionSpec, context: AIContext) -> str:
        if spec.kind is AIActionKind.CONTROL_MODE:
            if mode_for(spec.action) is None:
                return "UNKNOWN MODE"
            if context.control_mode == spec.action:
                return f"ALREADY IN {spec.action} MODE"
            return ""
        if spec.kind is AIActionKind.DEVICE_ACTION:
            # Device actions address the device layer; they are legal in either
            # mode, exactly like the device panel buttons, but the layer itself
            # still has to be enabled (step 2).
            return ""
        return ""

    @staticmethod
    def _enabled_reason(spec: AIActionSpec, context: AIContext) -> str:
        if context.emergency:
            return ""  # reported by the emergency gate, with its own wording
        if spec.kind is AIActionKind.DEVICE_ACTION:
            if not context.device_enabled:
                return "DEVICE CONTROL NOT ENABLED"
            return ""
        if spec.action == "PAUSE_CONTROL":
            if not context.mouse_enabled:
                return "MOUSE CONTROL NOT ENABLED"
            if context.mouse_paused:
                return "CONTROL ALREADY PAUSED"
            return ""
        if spec.action == "RESUME_CONTROL":
            if not context.mouse_paused:
                return "CONTROL NOT PAUSED"
            return ""
        return ""

    @staticmethod
    def _capability_reason(spec: AIActionSpec, context: AIContext) -> str:
        if spec.device_action is None:
            return ""
        capability = spec.device_action.capability.value
        available = context.capability_map().get(capability)
        if available is None:
            return f"{capability} NOT REPORTED BY THIS SYSTEM"
        if not available:
            return f"{capability} UNAVAILABLE ON THIS SYSTEM"
        return ""

    # -- result ------------------------------------------------------------ #

    def _report(self, plan: AIActionPlan, outcome: IntentOutcome) -> AIActionOutcome:
        """Translate a routed intent into the honest AI-visible result."""
        if not outcome.accepted or not outcome.handled:
            self.refused += 1
            return AIActionOutcome(
                plan,
                accepted=outcome.accepted,
                executed=outcome.handled,
                success=False,
                message=f"{plan.label} FAILED",
                reason=outcome.reason or "REFUSED",
            )

        self.executed += 1
        success, message, detail = self._real_result(plan)
        return AIActionOutcome(
            plan,
            accepted=True,
            executed=True,
            success=success,
            message=message,
            reason="" if success else detail,
        )

    def _real_result(self, plan: AIActionPlan) -> Tuple[bool, str, str]:
        """Read the controller's own verdict instead of assuming success."""
        if plan.kind is not AIActionKind.DEVICE_ACTION or self._telemetry is None:
            return True, plan.label, ""
        telemetry = self._telemetry()
        label = getattr(telemetry, "device_action_label", "") or plan.label
        success = bool(getattr(telemetry, "device_action_success", False))
        detail = getattr(telemetry, "device_action_detail", "") or ""
        return success, label, detail
