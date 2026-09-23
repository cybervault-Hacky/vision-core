"""Futuristic HUD layout and telemetry visualizer for VisionCore."""

from __future__ import annotations

import os
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.ai.types import AIStatus
from app.controls import ControlAction, ControlMode, ControlState
from app.gestures import Gesture, GesturePhase, GestureState
from app.state import SubsystemState, Telemetry
from ui.animations import PulseAnimation

# Visual Palette
COLOR_BG_DARK = (8, 12, 18)
COLOR_PANEL_BG = (13, 22, 35)
COLOR_PANEL_BORDER = (24, 46, 72)
COLOR_CYAN_PRIMARY = (0, 229, 255)
COLOR_ICE_BLUE = (140, 215, 255)
COLOR_TEXT_WHITE = (235, 242, 250)
COLOR_TEXT_MUTED = (120, 145, 170)

# Minimum pixel spacing between metric rows; below this the layout drops
# supplementary rows instead of overlapping them.
MIN_ROW_STEP = 12

# Width of the footer assistant button, reserved so the legend never collides.
AI_BUTTON_WIDTH = 150

COLOR_ONLINE = (0, 245, 160)      # Mint green
COLOR_STANDBY = (255, 183, 3)     # Amber
COLOR_DISABLED = (95, 115, 130)   # Neutral slate
COLOR_ERROR = (255, 60, 90)       # Coral red

def get_status_color(status: SubsystemState) -> Tuple[int, int, int]:
    """Return theme color matching the subsystem status."""
    if status in (SubsystemState.ONLINE, SubsystemState.ACTIVE):
        return COLOR_ONLINE
    if status in (SubsystemState.STANDBY, SubsystemState.CHECKING, SubsystemState.INITIALIZING):
        return COLOR_STANDBY
    if status is SubsystemState.SEARCHING:
        return COLOR_ICE_BLUE
    if status is SubsystemState.LOST:
        return COLOR_STANDBY
    if status is SubsystemState.DISABLED:
        return COLOR_DISABLED
    return COLOR_ERROR


# Fallback labels for the allowlisted launcher buttons; the live labels come from
# the launcher itself so the panel only offers applications that resolved.
_LAUNCH_KEYS = {
    "browser": "BROWSER",
    "calculator": "CALC",
    "files": "FILES",
}

# Real assistant status -> colour. Shared with the AI panel so the footer button
# and the panel can never disagree about what the assistant is doing.
_AI_STATUS_COLOR = {
    AIStatus.READY: (0, 245, 160),
    AIStatus.NOT_CONFIGURED: (95, 115, 130),
    AIStatus.THINKING: (0, 229, 255),
    AIStatus.RESPONDING: (140, 215, 255),
    AIStatus.EXECUTING: (255, 183, 3),
    AIStatus.ERROR: (255, 60, 90),
}


def ai_status_color(status: AIStatus) -> Tuple[int, int, int]:
    """Colour for a real assistant status."""
    return _AI_STATUS_COLOR.get(status, COLOR_TEXT_MUTED)


# Short guidance shown while control is idle.
_CONTROL_HINT = {
    ControlState.DISABLED: "POINT TO CONTROL",
    ControlState.ARMED: "POINT TO ENGAGE",
    ControlState.ACTIVE: "PINCH CLICK / HOLD DRAG",
    ControlState.PAUSED: "TRACKING CONTINUES",
    ControlState.EMERGENCY_STOP: "OPEN PALM STOPPED IT",
}


class HUDManager:
    """Manages HUD rendering, telemetry sidebars, header, and system matrix."""

    def __init__(self) -> None:
        self._pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.2)

    def update(self, dt: float) -> None:
        self._pulse.update(dt)

    # -- containers -------------------------------------------------------- #

    def draw_chamfer_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        bg_color: Tuple[int, int, int] = COLOR_PANEL_BG,
        border_color: Tuple[int, int, int] = COLOR_PANEL_BORDER,
        chamfer: int = 8,
        border_width: int = 1,
    ) -> None:
        """Render a sci-fi chamfered corner container."""
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        c = min(chamfer, w // 4, h // 4)

        # Polygon points with chamfered top-left and bottom-right corners
        points = [
            (x + c, y),
            (x + w, y),
            (x + w, y + h - c),
            (x + w - c, y + h),
            (x, y + h),
            (x, y + c),
        ]

        pygame.draw.polygon(surface, bg_color, points)

        if border_width > 0:
            pygame.draw.polygon(surface, border_color, points, border_width)

        pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x, y + c), (x + c, y), 2)
        pygame.draw.line(
            surface,
            COLOR_CYAN_PRIMARY,
            (x + w - c, y + h),
            (x + w, y + h - c),
            2,
        )

    def draw_panel_header(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        title: str,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render a panel title and its divider."""
        header_surf = fonts["subheading"].render(title, True, COLOR_CYAN_PRIMARY)
        surface.blit(header_surf, (rect.left + 16, rect.top + 12))
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left + 16, rect.top + 34),
            (rect.right - 16, rect.top + 34),
            1,
        )

    def draw_metric_rows(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        rows: Sequence[Tuple[str, str, Tuple[int, int, int]]],
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Draw label/value rows with spacing that adapts to the panel height.

        The row count is clamped to what the panel can show, so a short window
        hides supplementary rows instead of writing them over the panel border or
        the diagnostics footer.
        """
        top = rect.top + 46
        available = max(1, (rect.bottom - 10) - top)
        capacity = max(1, available // MIN_ROW_STEP)
        rows = rows[:capacity]
        step = max(MIN_ROW_STEP, min(24, available // max(1, len(rows))))
        y = top

        for label, value, color in rows:
            label_surf = fonts["caption"].render(label, True, COLOR_TEXT_MUTED)
            surface.blit(label_surf, (rect.left + 16, y))

            value_surf = fonts["mono"].render(value, True, color)
            value_rect = value_surf.get_rect(right=rect.right - 16, centery=y + 7)
            surface.blit(value_surf, value_rect)
            y += step

    # -- header & footer --------------------------------------------------- #

    def draw_header(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render top navigational & branding bar."""
        pygame.draw.rect(surface, (10, 16, 26), rect)
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left, rect.bottom - 1),
            (rect.right, rect.bottom - 1),
            1,
        )

        glow_alpha = int(180 * self._pulse.value)
        accent_surf = pygame.Surface((rect.width, 2), pygame.SRCALPHA)
        accent_surf.fill((*COLOR_CYAN_PRIMARY, glow_alpha))
        surface.blit(accent_surf, (rect.left, rect.bottom - 2))

        title_surf = fonts["title"].render("VISIONCORE", True, COLOR_TEXT_WHITE)
        surface.blit(title_surf, (rect.left + 20, rect.top + 10))

        # No capability claim beyond what the project actually does: on-device
        # landmark inference, no cloud service, no hosted model.
        sub_surf = fonts["caption"].render(
            "LOCAL VISION INTERFACE // ON-DEVICE INFERENCE", True, COLOR_CYAN_PRIMARY
        )
        surface.blit(sub_surf, (rect.left + 20, rect.top + 34))

        self._draw_interaction_chip(surface, rect, telemetry, fonts)

        badge_x = rect.right - 250
        badge_y = rect.top + 12

        dot_color = tuple(int(channel * self._pulse.value) for channel in COLOR_ONLINE)
        pygame.draw.circle(surface, dot_color, (badge_x, badge_y + 8), 5)

        badge_text = fonts["subheading"].render("SYSTEM ONLINE", True, COLOR_ONLINE)
        surface.blit(badge_text, (badge_x + 14, badge_y))

        uptime_text = fonts["mono"].render(
            f"UPTIME {telemetry.formatted_uptime}", True, COLOR_TEXT_MUTED
        )
        surface.blit(uptime_text, (badge_x + 14, badge_y + 20))

    def _draw_interaction_chip(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Header chip showing the resolved interaction state and control mode.

        The state comes from the interaction director, which derives it purely
        from real subsystem state; the mode shown is the active control mode.
        """
        interaction = telemetry.interaction
        color = get_status_color(telemetry.control)
        if interaction.state.is_safety:
            color = COLOR_ERROR if interaction.state.value == "EMERGENCY_STOP" else COLOR_STANDBY

        chip_x = rect.right - 470
        if chip_x < rect.left + 360:
            return

        dot_alpha = 150 + int(105 * self._pulse.value)
        pygame.draw.circle(
            surface,
            tuple(int(channel * (dot_alpha / 255.0)) for channel in color),
            (chip_x, rect.top + 20),
            4,
        )
        state_surf = fonts["mono"].render(interaction.state.label, True, color)
        surface.blit(state_surf, (chip_x + 12, rect.top + 12))

        mode_surf = fonts["mono_small"].render(
            f"{interaction.mode_label} MODE", True, COLOR_TEXT_MUTED
        )
        surface.blit(mode_surf, (chip_x + 12, rect.top + 30))

    def draw_footer_diagnostics(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render bottom status bar with engine version and hotkey prompts."""
        pygame.draw.rect(surface, (10, 15, 23), rect)
        pygame.draw.line(
            surface,
            COLOR_PANEL_BORDER,
            (rect.left, rect.top),
            (rect.right, rect.top),
            1,
        )

        left_text = (
            f"LOCAL ENGINE: ACTIVE  |  SECURE: ISOLATED (NO CLOUD UPLOADS)  |  "
            f"BACKEND: {telemetry.camera_backend}"
        )
        left_surf = fonts["mono_small"].render(left_text, True, COLOR_TEXT_MUTED)
        surface.blit(left_surf, (rect.left + 16, rect.top + 7))

        # Hotkey legend: shortened, then dropped, as the window narrows so it can
        # never run into the engine line on the left or the AI button on the right.
        legend = (
            "[A] AI  |  [C] CONTROL  |  [M] MOUSE  |  [D] DEVICE  |  [P] DIAGNOSTICS  |  "
            "[F11] FULLSCREEN  |  [ESC] SHUTDOWN"
        )
        if rect.width < 1000:
            legend = "[A] AI  |  [C] CONTROL  |  [P] DIAGNOSTICS  |  [ESC] SHUTDOWN"
        right_surf = fonts["mono_small"].render(legend, True, COLOR_CYAN_PRIMARY)
        legend_right = rect.right - AI_BUTTON_WIDTH - 28
        if (
            rect.width >= 780
            and legend_right - right_surf.get_width() > left_surf.get_width() + rect.left + 24
        ):
            right_rect = right_surf.get_rect(right=legend_right, centery=rect.top + 14)
            surface.blit(right_surf, right_rect)

    def draw_ai_button(
        self,
        surface: pygame.Surface,
        footer_rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        opened: bool = False,
    ) -> pygame.Rect:
        """The VISIONCORE AI button: deliberate activation, real status colour.

        The dot shows the assistant's actual state, so an unconfigured assistant
        looks unconfigured before the panel is even opened.
        """
        rect = pygame.Rect(
            footer_rect.right - 16 - AI_BUTTON_WIDTH,
            footer_rect.top + 4,
            AI_BUTTON_WIDTH,
            footer_rect.height - 8,
        )
        snapshot = telemetry.ai
        color = ai_status_color(snapshot.status)
        hovered = rect.collidepoint(pygame.mouse.get_pos())
        background = (14, 30, 46) if opened else ((12, 24, 38) if hovered else (10, 17, 26))
        border = COLOR_CYAN_PRIMARY if (opened or hovered) else COLOR_PANEL_BORDER
        pygame.draw.rect(surface, background, rect)
        pygame.draw.rect(surface, border, rect, 1)

        dot_color = tuple(int(channel * (0.55 + 0.45 * self._pulse.value)) for channel in color)
        pygame.draw.circle(surface, dot_color, (rect.left + 12, rect.centery), 3)

        label = fonts["mono_small"].render("VISIONCORE AI", True, COLOR_TEXT_WHITE)
        surface.blit(label, (rect.left + 22, rect.centery - label.get_height() // 2))
        return rect

    # -- panels ------------------------------------------------------------ #

    def draw_system_matrix_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render subsystem matrix showing live and standby module states."""
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "SUBSYSTEM MATRIX", fonts)

        subsystems = [
            ("VISION CORE", telemetry.vision_core),
            ("CAMERA", telemetry.camera),
            ("TRACKING", telemetry.tracking),
            ("GESTURES", telemetry.gestures),
            ("CONTROL", telemetry.control),
        ]

        rows: List[Tuple[str, str, Tuple[int, int, int]]] = []
        for name, status in subsystems:
            rows.append((name, status.value, get_status_color(status)))

        top = rect.top + 46
        available = max(1, (rect.bottom - 10) - top)
        capacity = max(1, available // MIN_ROW_STEP)
        rows = rows[:capacity]
        step = max(MIN_ROW_STEP, min(24, available // max(1, len(rows))))
        y = top

        for name, status, color in rows:
            label_surf = fonts["body"].render(name, True, COLOR_TEXT_WHITE)
            surface.blit(label_surf, (rect.left + 16, y))

            value_surf = fonts["mono"].render(status, True, color)
            value_rect = value_surf.get_rect(right=rect.right - 16, centery=y + 8)
            surface.blit(value_surf, value_rect)

            pygame.draw.circle(surface, color, (value_rect.left - 10, y + 8), 3)
            y += step

    def draw_tracking_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render live hand tracking telemetry (measured values only)."""
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "HAND TRACKING", fonts)

        state_label = telemetry.tracking_state.status_label
        state_color = get_status_color(telemetry.tracking)

        confidence = (
            f"{telemetry.hand_confidence * 100:.1f} %"
            if telemetry.hand_confidence is not None
            else "--"
        )

        rows = [
            ("STATE", state_label, state_color),
            ("HANDS", f"{telemetry.hands_detected} / {telemetry.max_hands}", COLOR_ICE_BLUE),
            ("HANDEDNESS", telemetry.hand_handedness or "--", COLOR_TEXT_WHITE),
            ("CONFIDENCE", confidence, COLOR_ICE_BLUE),
            (
                "INFERENCE",
                f"{telemetry.tracking_latency_ms:.1f} MS" if telemetry.tracking_latency_ms > 0 else "--",
                COLOR_TEXT_MUTED,
            ),
            (
                "PIPELINE",
                f"{telemetry.tracker_fps:.1f} HZ" if telemetry.tracker_fps > 0 else "--",
                COLOR_TEXT_MUTED,
            ),
        ]

        # Only surfaced when the host cannot keep up with the tracker.
        if telemetry.tracking_dropped_frames > 0:
            rows.append(("DROPPED", str(telemetry.tracking_dropped_frames), COLOR_STANDBY))

        self.draw_metric_rows(surface, rect, rows, fonts)

    def draw_gesture_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render recognised gesture state (measured evidence only)."""
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "GESTURE ENGINE", fonts)

        state = telemetry.gesture_state
        releasing = telemetry.gesture_phase is GesturePhase.RELEASE
        recognised = telemetry.gesture is not Gesture.NONE

        if state is GestureState.DISABLED:
            headline, accent = "DISABLED", COLOR_DISABLED
        elif releasing and recognised:
            headline, accent = telemetry.gesture.label, COLOR_STANDBY
        elif recognised:
            headline, accent = telemetry.gesture.label, COLOR_ONLINE
        elif state is GestureState.SEARCHING:
            headline, accent = "SEARCHING", COLOR_ICE_BLUE
        else:
            headline, accent = "ANALYZING", COLOR_ICE_BLUE

        headline_surface = fonts["subheading"].render(headline, True, accent)
        surface.blit(headline_surface, (rect.left + 16, rect.top + 44))

        if recognised or releasing:
            dot_x = rect.right - 22
            pygame.draw.circle(
                surface,
                accent,
                (dot_x, rect.top + 54),
                4,
            )
            pygame.draw.circle(
                surface,
                tuple(int(channel * self._pulse.value) for channel in accent),
                (dot_x, rect.top + 54),
                int(5 + 3 * self._pulse.value),
                1,
            )

        confidence = (
            f"{telemetry.gesture_confidence * 100:.1f} %"
            if telemetry.gesture_confidence is not None
            else "--"
        )
        rows = [
            ("CONFIDENCE", confidence, COLOR_ICE_BLUE),
            ("STATE", "RELEASE" if releasing else state.display_label, accent),
            (
                "LATENCY",
                f"{telemetry.gesture_latency_ms:.2f} MS" if telemetry.gesture_latency_ms > 0 else "--",
                COLOR_TEXT_MUTED,
            ),
            ("EVENTS", str(telemetry.gesture_events), COLOR_TEXT_MUTED),
        ]

        body = pygame.Rect(rect.x, rect.y + 32, rect.width, rect.height - 32)
        self.draw_metric_rows(surface, body, rows, fonts)

    def draw_control_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> Tuple[Optional[pygame.Rect], Optional[pygame.Rect]]:
        """Render the mouse control module for the active control mode.

        The rows are contextual: in MOUSE mode they report the real readiness of
        POINTER, CLICK and SCROLL, and in DEVICE mode they report that the mouse
        layer is suspended, which is exactly what the controller is doing. A
        capability the backend does not provide is never shown as ready.

        Returns the rectangles of the two interactive controls (primary toggle and
        disable) so the window can route clicks to them, or ``(None, None)`` when
        the panel is too small to host them.
        """
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "MOUSE CONTROL", fonts)

        state = telemetry.control_state
        accent = get_status_color(telemetry.control)

        headline = state.label
        if state is ControlState.EMERGENCY_STOP:
            accent = COLOR_ERROR

        headline_surf = fonts["subheading"].render(headline, True, accent)
        surface.blit(headline_surf, (rect.left + 16, rect.top + 44))
        pygame.draw.circle(
            surface,
            tuple(int(c * self._pulse.value) for c in accent),
            (rect.right - 22, rect.top + 54),
            int(4 + 2 * self._pulse.value),
        )

        device_mode = telemetry.control_mode is ControlMode.DEVICE

        # Live indicator: what the control layer is doing right now.
        if state is ControlState.EMERGENCY_STOP:
            indicator, indicator_color = "SAFETY STOP", COLOR_ERROR
        elif device_mode:
            indicator, indicator_color = "SUSPENDED // DEVICE MODE", COLOR_DISABLED
        elif telemetry.pointer_dragging:
            indicator, indicator_color = "DRAGGING", COLOR_ONLINE
        elif telemetry.pointer_scrolling:
            indicator, indicator_color = "SCROLLING", COLOR_ONLINE
        elif telemetry.pointer_active:
            indicator, indicator_color = "POINTER ACTIVE", COLOR_ONLINE
        elif telemetry.control_suspended:
            indicator, indicator_color = "SUSPENDED", COLOR_STANDBY
        elif state is ControlState.PAUSED:
            indicator, indicator_color = "PAUSED", COLOR_STANDBY
        elif state is ControlState.ARMED:
            indicator, indicator_color = "READY", COLOR_STANDBY
        else:
            indicator, indicator_color = "CONTROL INACTIVE", COLOR_DISABLED

        indicator_surf = fonts["mono_small"].render(indicator, True, indicator_color)
        surface.blit(indicator_surf, (rect.left + 16, rect.top + 66))

        # Contextual readiness rows, drawn only when the module is tall enough.
        rows = self._readiness_rows(telemetry, state, device_mode)
        y = rect.top + 86
        for label, value, color in rows:
            if y + 12 > rect.bottom - 36:
                break
            row_label = fonts["caption"].render(label, True, COLOR_TEXT_MUTED)
            surface.blit(row_label, (rect.left + 16, y))
            row_value = fonts["mono_small"].render(value, True, color)
            surface.blit(row_value, row_value.get_rect(right=rect.right - 16, top=y))
            y += 15

        # Secondary line: the last real action, the safety reason or a backend
        # hint - dropped first when the module is short.
        if rect.height >= 176:
            if telemetry.control_message:
                note, note_color = telemetry.control_message, COLOR_STANDBY
            elif telemetry.control_action is not ControlAction.NONE:
                note = telemetry.control_action.value
                note_color = COLOR_ICE_BLUE
            else:
                note = _CONTROL_HINT.get(state, "")
                note_color = COLOR_TEXT_MUTED
            if note:
                note_surf = fonts["mono_small"].render(note, True, note_color)
                surface.blit(note_surf, (rect.left + 16, rect.bottom - 52))

        return self._draw_control_buttons(surface, rect, telemetry, fonts, state)

    @staticmethod
    def _readiness_rows(
        telemetry: Telemetry,
        state: ControlState,
        device_mode: bool,
    ) -> List[Tuple[str, str, Tuple[int, int, int]]]:
        """Real readiness of POINTER, CLICK and SCROLL for the active mode.

        Every value comes from the control layer's own report: an engaged
        pointer, an unsupported button backend, a paused layer or a disarmed
        layer each produce their own honest reading.
        """
        if state is ControlState.EMERGENCY_STOP:
            return [
                ("POINTER", "BLOCKED", COLOR_ERROR),
                ("CLICK", "BLOCKED", COLOR_ERROR),
                ("SCROLL", "BLOCKED", COLOR_ERROR),
            ]
        if device_mode:
            return [
                ("POINTER", "SUSPENDED", COLOR_DISABLED),
                ("CLICK", "SUSPENDED", COLOR_DISABLED),
                ("SCROLL", "SUSPENDED", COLOR_DISABLED),
            ]

        supported = "CLICKS UNAVAILABLE" not in telemetry.control_message
        armed = state is ControlState.ARMED or state is ControlState.ACTIVE
        paused = state is ControlState.PAUSED

        if telemetry.pointer_active:
            pointer = ("ACTIVE", COLOR_ONLINE)
        elif paused:
            pointer = ("PAUSED", COLOR_STANDBY)
        elif not telemetry.control_available:
            pointer = ("UNAVAILABLE", COLOR_DISABLED)
        elif armed:
            pointer = ("READY", COLOR_ICE_BLUE)
        else:
            pointer = ("OFF", COLOR_DISABLED)

        if not supported or not telemetry.control_available:
            click = ("UNAVAILABLE", COLOR_DISABLED)
        elif telemetry.pointer_dragging:
            click = ("DRAGGING", COLOR_ONLINE)
        elif paused:
            click = ("PAUSED", COLOR_STANDBY)
        elif armed:
            click = ("READY", COLOR_ICE_BLUE)
        else:
            click = ("OFF", COLOR_DISABLED)

        if not supported or not telemetry.control_available:
            scroll = ("UNAVAILABLE", COLOR_DISABLED)
        elif telemetry.pointer_scrolling:
            scroll = ("ACTIVE", COLOR_ONLINE)
        elif paused:
            scroll = ("PAUSED", COLOR_STANDBY)
        elif armed:
            scroll = ("READY", COLOR_ICE_BLUE)
        else:
            scroll = ("OFF", COLOR_DISABLED)

        return [("POINTER", *pointer), ("CLICK", *click), ("SCROLL", *scroll)]

    def _draw_control_buttons(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        state: ControlState,
    ) -> Tuple[Optional[pygame.Rect], Optional[pygame.Rect]]:
        """Clickable control rectangles: primary toggle plus an explicit disable."""
        # The controls are reserved space at the bottom of the module: they must
        # stay clickable even when the window is short.
        top = rect.bottom - 34
        if rect.height < 112:
            return None, None

        disabled = state is ControlState.DISABLED
        two_buttons = not disabled
        gap = 8
        width = rect.width - 32 if not two_buttons else (rect.width - 32 - gap) // 2
        primary = pygame.Rect(rect.left + 16, top, width, 26)
        secondary = pygame.Rect(primary.right + gap, top, width, 26) if two_buttons else None

        mouse = pygame.mouse.get_pos()
        enabled = telemetry.control_available
        if disabled:
            label = "ENABLE" if enabled else "UNAVAILABLE"
        elif state in (ControlState.ARMED, ControlState.ACTIVE):
            label = "PAUSE"
        else:
            label = "RESUME"

        self._draw_control_button(
            surface, primary, label, fonts, enabled, primary.collidepoint(mouse)
        )
        if secondary is not None:
            self._draw_control_button(
                surface, secondary, "DISABLE", fonts, True, secondary.collidepoint(mouse)
            )
        return primary, secondary

    @staticmethod
    def _draw_control_button(
        surface: pygame.Surface,
        rect: pygame.Rect,
        label: str,
        fonts: Dict[str, pygame.font.Font],
        enabled: bool,
        hovered: bool = False,
    ) -> None:
        """Small flat control button matching the interface language."""
        if not enabled:
            border = COLOR_DISABLED
            text = COLOR_DISABLED
            background = (10, 16, 24)
        elif hovered:
            border = COLOR_CYAN_PRIMARY
            text = COLOR_TEXT_WHITE
            background = (18, 36, 54)
        else:
            border = COLOR_CYAN_PRIMARY
            text = COLOR_CYAN_PRIMARY
            background = (12, 24, 38)
        pygame.draw.rect(surface, background, rect)
        pygame.draw.rect(surface, border, rect, 1)
        text_surf = fonts["mono_small"].render(label, True, text)
        surface.blit(text_surf, text_surf.get_rect(center=rect.center))

    def draw_device_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> Dict[str, Optional[pygame.Rect]]:
        """Render the device control module.

        Returns the clickable rectangles keyed by role so the window can route
        clicks: ``mode``, ``toggle`` and the window action and launcher buttons.
        Controls the platform cannot provide are drawn disabled and are not
        returned. Every row is placed from the panel height, so nothing is ever
        written over the border at any window size.
        """
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "DEVICE CONTROL", fonts)

        state = telemetry.device_state
        accent = get_status_color(telemetry.device)
        if state is ControlState.EMERGENCY_STOP:
            accent = COLOR_ERROR

        device_mode = telemetry.control_mode is ControlMode.DEVICE
        capabilities = telemetry.device_capabilities

        volume, volume_color = self._volume_readout(telemetry, capabilities)
        brightness, brightness_color = self._level_readout(
            telemetry.device_brightness, telemetry.device_brightness_known, capabilities.get("BRIGHTNESS")
        )
        # The device layer is armed but deliberately inert outside DEVICE mode,
        # and a capability the platform does not expose is never shown as ready.
        if state is ControlState.DISABLED:
            status_text, status_color = state.label, accent
        elif not device_mode:
            status_text, status_color = "SUSPENDED", COLOR_STANDBY
        elif state is ControlState.EMERGENCY_STOP:
            status_text, status_color = state.label, COLOR_ERROR
        else:
            status_text, status_color = state.label, accent

        fields = (
            ("MODE", telemetry.control_mode.label,
             COLOR_ICE_BLUE if device_mode else COLOR_TEXT_MUTED),
            ("STATUS", status_text, status_color),
            ("VOLUME", volume, volume_color),
            ("MEDIA", *self._capability_readout(capabilities.get("MEDIA"))),
            ("BRIGHTNESS", brightness, brightness_color),
            ("WINDOW", *self._capability_readout(capabilities.get("WINDOW"))),
        )
        grid_height = self._draw_field_grid(surface, rect, fields, fonts)

        # Persistent reason for a capability the host does not provide, so the
        # module explains itself without the user having to trigger the action.
        notice_top = self._draw_capability_note(
            surface, rect, telemetry, fonts, rect.top + grid_height
        )
        buttons = self._draw_device_buttons(surface, rect, telemetry, fonts, state, device_mode)
        topmost = buttons["launch"] or buttons["minimize"] or buttons["toggle"]
        notice_bottom = (topmost.top - 4) if topmost is not None else (rect.bottom - 8)
        self._draw_device_notice(surface, rect, telemetry, fonts, notice_top, notice_bottom)
        return buttons

    def _draw_capability_note(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        top: int,
    ) -> int:
        """Report why an unavailable capability is unavailable, if the panel fits it.

        The text comes from the platform's own capability report, so the
        interface explains a missing feature instead of just greying it out.
        """
        if top + 14 > rect.bottom - 8:
            return top
        unavailable = [
            (label, telemetry.device_capability_notes.get(label, ""))
            for label, available in telemetry.device_capabilities.items()
            if not available
        ]
        if not unavailable:
            return top
        label, reason = unavailable[0]
        text = f"{label} NOT SUPPORTED"
        if reason:
            text += f" / {reason}"
        note = fonts["mono_small"].render(text[:46], True, COLOR_DISABLED)
        surface.blit(note, (rect.left + 16, top))
        return top + 13

    def _volume_readout(
        self, telemetry: Telemetry, capabilities: Mapping[str, bool]
    ) -> Tuple[str, Tuple[int, int, int]]:
        """Volume text: measured level, relative counter, or honest UNAVAILABLE."""
        if not capabilities.get("VOLUME"):
            return "UNAVAILABLE", COLOR_DISABLED
        if telemetry.device_volume_known and telemetry.device_volume is not None:
            level = f"{telemetry.device_volume * 100:.0f}%"
            if telemetry.device_muted:
                level = f"{level} MUTED"
            return level, COLOR_ICE_BLUE
        steps = telemetry.device_volume_steps
        # The platform changes the volume but cannot report the level; show the
        # relative change rather than inventing a percentage.
        return (f"STEP {steps:+d}" if steps else "RELATIVE"), COLOR_ICE_BLUE

    @staticmethod
    def _level_readout(
        level: Optional[float], known: bool, supported: Optional[bool]
    ) -> Tuple[str, Tuple[int, int, int]]:
        if not supported:
            return "UNAVAILABLE", COLOR_DISABLED
        if known and level is not None:
            return f"{level * 100:.0f}%", COLOR_ICE_BLUE
        return "RELATIVE", COLOR_ICE_BLUE

    @staticmethod
    def _capability_readout(supported: Optional[bool]) -> Tuple[str, Tuple[int, int, int]]:
        if supported:
            return "READY", COLOR_ONLINE
        return "UNAVAILABLE", COLOR_DISABLED

    def _draw_field_grid(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        fields: Sequence[Tuple[str, str, Tuple[int, int, int]]],
        fonts: Dict[str, pygame.font.Font],
    ) -> int:
        """Draw label/value fields in two columns and return the grid height.

        Six short fields fit in three compact rows, so the module stays readable
        in a short window while still reporting every control it owns.
        """
        step = 15
        column_width = (rect.width - 32 - 12) // 2
        top = rect.top + 40
        for index, (label, value, color) in enumerate(fields):
            column = index % 2
            row = index // 2
            x = rect.left + 16 + column * (column_width + 12)
            y = top + row * step
            label_surf = fonts["caption"].render(label, True, COLOR_TEXT_MUTED)
            surface.blit(label_surf, (x, y))
            value_surf = fonts["mono_small"].render(value, True, color)
            value_rect = value_surf.get_rect(right=x + column_width, top=y)
            if value_rect.left < x + label_surf.get_width() + 6:
                value_rect.left = x + label_surf.get_width() + 6
            surface.blit(value_surf, value_rect)
        return 40 + step * ((len(fields) + 1) // 2) + 2

    def _draw_device_notice(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        top: int,
        bottom: int,
    ) -> None:
        """Fading notification for the last device action or safety message."""
        if bottom - top < 12:
            return

        fade = max(0.0, 1.0 - telemetry.device_action_age / 1.6)
        if telemetry.device_suspended and telemetry.device_suspended_reason:
            text, color, alpha = telemetry.device_suspended_reason, COLOR_STANDBY, 255
        elif telemetry.device_message:
            text, color, alpha = telemetry.device_message, COLOR_STANDBY, 255
        elif telemetry.device_action_label and fade > 0.02:
            text = telemetry.device_action_label
            color = COLOR_ONLINE if telemetry.device_action_success else COLOR_ERROR
            alpha = int(255 * min(1.0, fade * 1.6))
        else:
            return

        rendered = fonts["mono_small"].render(text, True, color)
        rendered.set_alpha(alpha)
        surface.blit(rendered, (rect.left + 16, top))
        if telemetry.device_action is not None and alpha > 40:
            # Draining underline: the notification fades out on its own.
            width = int(rendered.get_width() * min(1.0, max(fade, 0.05)))
            pygame.draw.line(surface, color, (rect.left + 16, top + 13),
                             (rect.left + 16 + width, top + 13), 1)

    def _draw_device_buttons(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        state: ControlState,
        device_mode: bool,
    ) -> Dict[str, Optional[pygame.Rect]]:
        """Mode, enable, window action and launcher buttons, as height allows."""
        buttons: Dict[str, Optional[pygame.Rect]] = {
            "mode": None,
            "toggle": None,
            "minimize": None,
            "maximize": None,
            "switch": None,
            "launch": None,
        }
        if rect.height < 130:
            return buttons

        mouse = pygame.mouse.get_pos()
        gap = 8
        enabled = telemetry.device_available
        width = (rect.width - 32 - gap) // 2

        toggle_row = pygame.Rect(rect.left + 16, rect.bottom - 32, width, 24)
        toggle_label = (
            "ENABLE" if state is ControlState.DISABLED
            else "PAUSE" if state in (ControlState.ARMED, ControlState.ACTIVE)
            else "RESUME"
        )
        if state is ControlState.DISABLED and not enabled:
            toggle_label = "UNAVAILABLE"
        mode_rect = pygame.Rect(toggle_row.left, toggle_row.top, width, 24)
        self._draw_control_button(
            surface, mode_rect, "MOUSE MODE" if device_mode else "DEVICE MODE", fonts,
            enabled, mode_rect.collidepoint(mouse),
        )
        buttons["mode"] = mode_rect
        toggle_rect = pygame.Rect(mode_rect.right + gap, toggle_row.top, width, 24)
        self._draw_control_button(
            surface, toggle_rect, toggle_label, fonts, enabled, toggle_rect.collidepoint(mouse)
        )
        buttons["toggle"] = toggle_rect

        if rect.height < 160:
            return buttons

        window_available = bool(telemetry.device_capabilities.get("WINDOW"))
        window_row = pygame.Rect(rect.left + 16, toggle_row.top - 26, rect.width - 32, 22)
        cell = (window_row.width - gap * 2) // 3
        for index, (key, label) in enumerate((("minimize", "MIN"), ("maximize", "MAX"), ("switch", "SWITCH"))):
            cell_rect = pygame.Rect(window_row.left + index * (cell + gap), window_row.top, cell, 22)
            if window_available:
                self._draw_control_button(
                    surface, cell_rect, label, fonts, enabled, cell_rect.collidepoint(mouse)
                )
                buttons[key] = cell_rect
            else:
                self._draw_control_button(surface, cell_rect, "--", fonts, False, False)

        if rect.height < 186 or window_row.top - 26 < rect.top + 101:
            return buttons

        launcher_row = pygame.Rect(window_row.left, window_row.top - 26, window_row.width, 22)
        # Only the applications this platform actually resolved are offered.
        entries = list(telemetry.device_launchable.items())[:3] or list(_LAUNCH_KEYS.items())[:3]
        cell = (launcher_row.width - gap * 2) // 3
        launchable = bool(telemetry.device_capabilities.get("LAUNCHER"))
        for index, (key, label) in enumerate(entries):
            cell_rect = pygame.Rect(launcher_row.left + index * (cell + gap), launcher_row.top, cell, 22)
            if launchable:
                self._draw_control_button(
                    surface, cell_rect, label, fonts, enabled, cell_rect.collidepoint(mouse)
                )
                buttons["launch"] = cell_rect if index == 0 else buttons["launch"]
                buttons[f"launch:{key}"] = cell_rect
            else:
                self._draw_control_button(surface, cell_rect, "--", fonts, False, False)
        return buttons

    def draw_camera_status_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render active camera stream telemetry and hardware parameters."""
        self.draw_chamfer_panel(surface, rect)
        self.draw_panel_header(surface, rect, "CAMERA TELEMETRY", fonts)

        rows = [
            ("DEVICE INDEX", f"DEV #{telemetry.camera_index}", COLOR_ICE_BLUE),
            (
                "RESOLUTION",
                f"{telemetry.camera_width} x {telemetry.camera_height}"
                if telemetry.camera_width > 0
                else "N/A",
                COLOR_ICE_BLUE,
            ),
            (
                "CAPTURE FPS",
                f"{telemetry.camera_fps:.1f} FPS" if telemetry.camera_fps > 0 else "STANDBY",
                COLOR_ICE_BLUE,
            ),
            ("RENDER FPS", f"{telemetry.render_fps:.1f} FPS", COLOR_ICE_BLUE),
            ("ORIENTATION", "MIRRORED" if telemetry.mirrored else "DIRECT", COLOR_ICE_BLUE),
            ("FRAMES RECV", f"{telemetry.frame_count:,}", COLOR_TEXT_MUTED),
        ]
        self.draw_metric_rows(surface, rect, rows, fonts)
