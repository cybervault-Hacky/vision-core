"""The Vision workspace: the camera viewport and its minimal control bar.

This is the primary screen. The camera feed dominates the page inside a large
rounded well; below it sits one compact control bar with only the controls that
matter in the current state - the control mode, the primary control action
(enable, pause, resume or recover) and the emergency stop.

In the camera-error state the same page becomes a calm recovery screen: the
wordmark, a clear statement of what happened, concise guidance and working
buttons. Technical diagnostics stay available but visually secondary.

Every control raises an intent that the window routes to the existing
application callbacks; this page performs nothing itself.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.controls.safety import ControlMode, ControlState
from app.gestures import GestureSnapshot
from app.hand_tracking import TrackingSnapshot
from app.interaction import RecoveryAction
from app.state import AppState, Telemetry
from ui import icons
from ui.camera_view import CameraView
from ui.theme import (
    COLOR_DANGER,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    control_state_color,
    draw_button,
    draw_segmented,
    fit,
    wrap,
)

CONTROL_BAR_HEIGHT = 56
CONTROL_BAR_GAP = 14


class VisionPage:
    """Camera viewport, control bar and the camera recovery screen."""

    def __init__(self) -> None:
        self.camera_view = CameraView()
        self._actions: Dict[str, pygame.Rect] = {}
        self._segment_hits: list = []
        self._recovery: Optional[Tuple[RecoveryAction, pygame.Rect]] = None

    # -- lifecycle --------------------------------------------------------- #

    def update(
        self,
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
    ) -> None:
        self.camera_view.update(dt, tracking, gesture)

    def take_recovery(self) -> RecoveryAction:
        """The recovery belonging to the viewport RETRY button, if any."""
        return self._recovery[0] if self._recovery is not None else RecoveryAction.CAMERA

    # -- events ------------------------------------------------------------ #

    def handle_click(self, position: Tuple[int, int]) -> Optional[str]:
        """Resolve a click into a page action token."""
        if self._recovery is not None and self._recovery[1].collidepoint(position):
            return "viewport_retry"
        for index, rect in enumerate(self._segment_hits):
            if rect.collidepoint(position):
                return "mode:MOUSE" if index == 0 else "mode:DEVICE"
        for action, rect in self._actions.items():
            if rect.collidepoint(position):
                return action
        return None

    # -- rendering --------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        frame,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
    ) -> None:
        """Draw the workspace for the current application state."""
        self._actions = {}
        self._segment_hits = []
        self._recovery = None

        if telemetry.app_state is AppState.CAMERA_ERROR:
            self._draw_error_state(surface, rect, telemetry, fonts)
            return

        control_bar = pygame.Rect(
            rect.left, rect.bottom - CONTROL_BAR_HEIGHT, rect.width, CONTROL_BAR_HEIGHT
        )
        viewport = pygame.Rect(
            rect.left, rect.top, rect.width,
            max(120, control_bar.top - CONTROL_BAR_GAP - rect.top),
        )

        self._recovery = self.camera_view.render(
            surface, viewport, frame, telemetry, fonts, tracking, gesture
        )
        self._draw_control_bar(surface, control_bar, telemetry, fonts)

    # -- control bar ------------------------------------------------------- #

    def _draw_control_bar(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        mouse = pygame.mouse.get_pos()
        device_mode = telemetry.control_mode is ControlMode.DEVICE

        # Control mode: a segmented control, only ever changed deliberately.
        segmented = pygame.Rect(rect.left, rect.centery - 16, 176, 32)
        hovered_index = -1
        for index, hit in enumerate(
            [pygame.Rect(segmented.left, segmented.top, segmented.width // 2, segmented.height),
             pygame.Rect(segmented.left + segmented.width // 2, segmented.top,
                         segmented.width - segmented.width // 2, segmented.height)]
        ):
            if hit.collidepoint(mouse):
                hovered_index = index
        self._segment_hits = draw_segmented(
            surface, segmented, ("Mouse", "Device"),
            1 if device_mode else 0, fonts, hovered_index,
        )

        # State of the active control layer, with its real colour.
        state = telemetry.device_state if device_mode else telemetry.control_state
        chip_x = segmented.right + 18
        dot = control_state_color(state)
        state_label = state.label.replace("_", " ").capitalize()
        state_surf = fonts["body"].render(state_label, True, COLOR_TEXT_DIM)
        surface.blit(state_surf, (chip_x + 12, rect.centery - state_surf.get_height() // 2))
        pygame.draw.circle(surface, dot, (int(chip_x + 4), rect.centery), 3)
        x = chip_x + 12 + state_surf.get_width() + 16

        # The layer's own note (a backend hint, a suspension reason), when it
        # fits between the state chip and the action buttons.
        message = telemetry.device_message if device_mode else telemetry.control_message
        if message:
            room = rect.right - 410 - x
            if room > 60:
                note = fonts["small"].render(
                    fit(message, fonts["small"], room), True, COLOR_TEXT_FAINT
                )
                surface.blit(note, (x, rect.centery - note.get_height() // 2))

        # Emergency stop / recovery, anchored right.
        stopped = state is ControlState.EMERGENCY_STOP
        if stopped:
            banner = fonts["small"].render("Stopped", True, COLOR_DANGER)
            surface.blit(
                banner,
                (rect.right - banner.get_width(),
                 rect.centery - banner.get_height() // 2),
            )
            note = fonts["small"].render(
                "recovery required", True, COLOR_TEXT_FAINT
            )
            surface.blit(
                note,
                (rect.right - 150 - note.get_width(), rect.centery - note.get_height() // 2),
            )
        else:
            stop = pygame.Rect(rect.right - 172, rect.centery - 18, 172, 36)
            draw_button(
                surface, stop, "Emergency stop", fonts, kind="danger",
                hovered=stop.collidepoint(mouse), icon="stop", icon_module=icons,
            )
            self._actions["emergency_stop"] = stop

        # Primary control action for the active layer, left of the stop button.
        primary_width = 104
        primary = pygame.Rect(
            rect.right - 172 - 14 - primary_width, rect.centery - 16, primary_width, 32
        )
        available = telemetry.device_available if device_mode else telemetry.control_available
        if state is ControlState.DISABLED:
            label = "Enable" if available else "Unavailable"
            enabled = available
        elif state in (ControlState.ARMED, ControlState.ACTIVE):
            label, enabled = "Pause", True
        else:
            # Paused or latched in an emergency stop: the deliberate way back.
            label, enabled = "Recover" if stopped else "Resume", True
        draw_button(
            surface, primary, label, fonts, kind="primary", enabled=enabled,
            hovered=primary.collidepoint(mouse) and enabled,
        )
        self._actions["toggle"] = primary

        # Disarming the mouse layer is a mouse-mode-only action.
        if not device_mode and state is not ControlState.DISABLED:
            disable = pygame.Rect(primary.left - 14 - 92, rect.centery - 16, 92, 32)
            draw_button(
                surface, disable, "Disable", fonts, kind="ghost",
                hovered=disable.collidepoint(mouse),
            )
            self._actions["disable"] = disable

    # -- camera recovery screen -------------------------------------------- #

    def _draw_error_state(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        telemetry: Telemetry,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """A calm, product-quality camera unavailable screen."""
        mouse = pygame.mouse.get_pos()
        card_width = min(560, rect.width - 32)
        card = pygame.Rect(
            rect.centerx - card_width // 2, rect.top, card_width, rect.height
        )

        y = card.top + max(24, int(card.height * 0.10))

        mark = icons.icon("camera", 30, COLOR_DANGER)
        surface.blit(mark, (card.centerx - mark.get_width() // 2, y))
        y += mark.get_height() + 18

        title = fonts["title"].render("Camera unavailable", True, COLOR_TEXT)
        surface.blit(title, (card.centerx - title.get_width() // 2, y))
        y += title.get_height() + 10

        message = telemetry.error_message or (
            f"No usable camera could be initialized at index {telemetry.camera_index}."
        )
        for line in wrap(message, fonts["body"], card_width - 72)[:3]:
            rendered = fonts["body"].render(line, True, COLOR_TEXT_DIM)
            surface.blit(rendered, (card.centerx - rendered.get_width() // 2, y))
            y += rendered.get_height() + 3
        y += 18

        # Concise, actionable guidance.
        heading = fonts["small"].render("Things to check", True, COLOR_TEXT_FAINT)
        surface.blit(heading, (card.left + 44, y))
        y += heading.get_height() + 10
        instructions = telemetry.error_instructions or [
            "Your camera is connected and powered on.",
            "Operating system camera permissions are granted.",
            "No other application is locking the camera.",
        ]
        for instruction in instructions[:4]:
            for line in wrap(instruction, fonts["caption"], card_width - 110)[:2]:
                text = fonts["caption"].render(line, True, COLOR_TEXT_DIM)
                surface.blit(text, (card.left + 62, y))
                y += text.get_height() + 2
            y += 6
        y += 10

        # Buttons: only ones that actually work.
        button_width = 148
        gap = 10
        total = button_width * 2 + gap
        retry = pygame.Rect(card.centerx - total // 2, y, button_width, 38)
        settings_rect = pygame.Rect(retry.right + gap, y, button_width, 38)
        draw_button(
            surface, retry, "Retry camera", fonts, kind="primary",
            hovered=retry.collidepoint(mouse), icon="refresh", icon_module=icons,
        )
        draw_button(
            surface, settings_rect, "Open settings", fonts,
            hovered=settings_rect.collidepoint(mouse), icon="gear", icon_module=icons,
        )
        self._actions["retry_camera"] = retry
        self._actions["open_settings"] = settings_rect
        y += 38 + 16

        exit_rect = pygame.Rect(card.centerx - 52, y, 104, 26)
        draw_button(
            surface, exit_rect, "Exit", fonts, kind="ghost",
            hovered=exit_rect.collidepoint(mouse),
        )
        self._actions["exit"] = exit_rect
        y += 26 + 14

        # Technical diagnostics: available, but visually secondary.
        detail = ""
        if telemetry.interaction.error is not None:
            detail = telemetry.interaction.error.detail
        if detail:
            text = fonts["mono_small"].render(
                fit(detail, fonts["mono_small"], card_width - 40), True, COLOR_TEXT_FAINT
            )
            surface.blit(text, (card.centerx - text.get_width() // 2, y))
