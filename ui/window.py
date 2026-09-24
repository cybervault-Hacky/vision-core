"""The application shell: window, top bar, navigation rail and pages.

The shell is a modern desktop application frame:

* a slim top bar with the wordmark on the left and live status indicators on
  the right - every indicator reflects real runtime state;
* a compact navigation rail on the left (Vision, AI, Controls, Settings) with
  outline icons and a quiet active state;
* a content area that hosts the active workspace, with a subtle page
  transition between them;
* a safety banner across the top of the content area while an emergency stop is
  latched, because that state must be visible from every page.

The shell owns no application logic. Every control on every page resolves to a
token that is routed here and mapped onto the application callbacks the window
was constructed with - the existing controllers, safety gates and assistant
pathways do all of the work.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.config import AppConfig
from app.controls import ControlMode, ControlState, DeviceAction
from app.gestures import GestureSnapshot
from app.hand_tracking import TrackingSnapshot
from app.interaction import RecoveryAction
from app.state import AppState, SubsystemState, Telemetry
from ui import icons
from ui.ai_page import AIPage
from ui.animations import PulseAnimation
from ui.boot_screen import BootScreen
from ui.controls_page import ControlsPage
from ui.settings_page import SettingsPage
from ui.shutdown_screen import ShutdownScreen
from ui.theme import (
    COLOR_ACCENT,
    COLOR_ACCENT_TINT,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_DANGER_TINT,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    NAV_WIDTH,
    TOP_BAR_HEIGHT,
    ai_status_color,
    draw_button,
    draw_panel,
    ease_out_cubic,
    fit,
    subsystem_status_color,
    voice_state_color,
)
from ui.vision_page import VisionPage
from version import VERSION

logger = logging.getLogger("visioncore.ui")

# Page transition (seconds). Subtle: a short rise, never a full slide.
TRANSITION_DURATION = 0.22
TRANSITION_RISE = 14

# Content padding inside the shell.
CONTENT_PAD_X = 20
CONTENT_PAD_TOP = 12
CONTENT_PAD_BOTTOM = 16

# Safety banner.
BANNER_HEIGHT = 48

# Navigation items: page, label, icon, keyboard shortcut.
NAV_ITEMS: Tuple[Tuple[str, str, str, str], ...] = (
    ("vision", "Vision", "eye", "1"),
    ("ai", "AI", "spark", "2"),
    ("controls", "Controls", "sliders", "3"),
    ("settings", "Settings", "gear", "4"),
)


class Page(Enum):
    """The four workspaces of the application."""

    VISION = "vision"
    AI = "ai"
    CONTROLS = "controls"
    SETTINGS = "settings"


class MainWindow:
    """Desktop window host, application shell and input event router."""

    def __init__(
        self,
        config: AppConfig,
        telemetry: Telemetry,
        on_retry_camera: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        on_control_toggle: Optional[Callable[[], None]] = None,
        on_control_disable: Optional[Callable[[], None]] = None,
        on_control_mode: Optional[Callable[[ControlMode], None]] = None,
        on_device_toggle: Optional[Callable[[], None]] = None,
        on_device_action: Optional[Callable[[DeviceAction, Optional[str]], None]] = None,
        on_recovery: Optional[Callable[[RecoveryAction], None]] = None,
        on_ai_send: Optional[Callable[[str], bool]] = None,
        on_ai_clear: Optional[Callable[[], None]] = None,
        on_ai_confirm: Optional[Callable[[], None]] = None,
        on_ai_cancel: Optional[Callable[[], None]] = None,
        on_voice_toggle: Optional[Callable[[], None]] = None,
        on_emergency_stop: Optional[Callable[[], None]] = None,
    ):
        self.config = config
        self.telemetry = telemetry
        self.on_retry_camera = on_retry_camera
        self.on_exit = on_exit
        self.on_control_toggle = on_control_toggle
        self.on_control_disable = on_control_disable
        self.on_control_mode = on_control_mode
        self.on_device_toggle = on_device_toggle
        self.on_device_action = on_device_action
        self.on_recovery = on_recovery
        self.on_ai_send = on_ai_send
        self.on_ai_clear = on_ai_clear
        self.on_ai_confirm = on_ai_confirm
        self.on_ai_cancel = on_ai_cancel
        self.on_voice_toggle = on_voice_toggle
        self.on_emergency_stop = on_emergency_stop

        self.width = max(config.min_window_width, config.window_width)
        self.height = max(config.min_window_height, config.window_height)
        self.is_running = False

        # Initialize Pygame display subsystem
        if not pygame.get_init():
            pygame.init()
        if not pygame.font.get_init():
            pygame.font.init()

        flags = pygame.RESIZABLE
        if config.fullscreen:
            flags |= pygame.FULLSCREEN

        self.surface = pygame.display.set_mode((self.width, self.height), flags)
        pygame.display.set_caption(f"VisionCore v{VERSION}")

        self.clock = pygame.time.Clock()
        self.fonts = self._init_fonts()

        # UI subcomponents
        self.boot_screen = BootScreen(duration_sec=config.boot_duration_sec)
        self.shutdown_screen = ShutdownScreen()
        self.pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.0)

        # Workspaces
        self.vision_page = VisionPage()
        self.ai_page = AIPage()
        self.controls_page = ControlsPage()
        self.settings_page = SettingsPage(config)
        self._pages = {
            Page.VISION: self.vision_page,
            Page.AI: self.ai_page,
            Page.CONTROLS: self.controls_page,
            Page.SETTINGS: self.settings_page,
        }

        self.page = Page.VISION
        self._nav_rects: List[Tuple[Page, pygame.Rect]] = []
        self._nav_hover: Optional[Page] = None
        self._banner_recover_rect: Optional[pygame.Rect] = None

        # Page transition state.
        self._transition = 1.0

    def _init_fonts(self) -> Dict[str, pygame.font.Font]:
        """The application type scale (see :mod:`ui.theme`)."""
        from ui.theme import build_fonts

        return build_fonts()

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    def switch_page(self, page: Page) -> None:
        """Switch the active workspace (an explicit user action)."""
        if page is self.page:
            return
        self.page = page
        self._transition = 0.0
        if page is not Page.AI:
            self.ai_page.clear_input()
        logger.info("Workspace switched to %s", page.value)

    def toggle_ai_page(self) -> None:
        """A opens the AI workspace; A again returns to the previous view."""
        self.switch_page(Page.VISION if self.page is Page.AI else Page.AI)

    def _toggle_fullscreen(self) -> None:
        """Fullscreen toggle (F11 and the Settings row share this path)."""
        self.config.fullscreen = not self.config.fullscreen
        flags = pygame.FULLSCREEN if self.config.fullscreen else pygame.RESIZABLE
        self.surface = pygame.display.set_mode((self.width, self.height), flags)
        logger.info("Fullscreen %s", "on" if self.config.fullscreen else "off")

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #

    def handle_events(self) -> bool:
        """Poll and handle window events. Returns False to terminate."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                logger.info("Window close event received")
                return False

            elif event.type == pygame.VIDEORESIZE:
                new_w = max(self.config.min_window_width, event.w)
                new_h = max(self.config.min_window_height, event.h)
                self.width = new_w
                self.height = new_h
                self.surface = pygame.display.set_mode((new_w, new_h), pygame.RESIZABLE)
                logger.debug("Window resized to %dx%d", new_w, new_h)

            elif event.type == pygame.KEYDOWN:
                if not self._handle_key(event):
                    return False

            elif event.type == pygame.MOUSEWHEEL:
                page = self._pages[self.page]
                if hasattr(page, "handle_wheel"):
                    page.handle_wheel(event.y)

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._handle_click(event.pos)

        return True

    def _handle_key(self, event: pygame.event.Event) -> bool:
        """Route one key event. Returns False when the app should quit."""
        if event.key == pygame.K_ESCAPE:
            logger.info("Escape key pressed -> quitting")
            return False
        if event.key == pygame.K_F11:
            self._toggle_fullscreen()
            return True

        # While the AI workspace is active every keystroke belongs to its
        # composer, so a typed "a" can never navigate away mid-sentence.
        if self.page is Page.AI:
            typing = bool(self.ai_page.input_text)
            if not typing and event.key == pygame.K_a:
                self.toggle_ai_page()
                return True
            if not typing and event.key == pygame.K_v:
                self._handle_voice_toggle()
                return True
            if not typing and event.unicode.isdigit() and event.unicode in "1234":
                self.switch_page(Page(NAV_ITEMS[int(event.unicode) - 1][0]))
                return True
            intent = self.ai_page.handle_key(event)
            if intent is not None:
                self._run_ai_intent(intent)
            return True

        if event.key == pygame.K_1:
            self.switch_page(Page.VISION)
        elif event.key == pygame.K_2:
            self.switch_page(Page.AI)
        elif event.key == pygame.K_3:
            self.switch_page(Page.CONTROLS)
        elif event.key == pygame.K_4:
            self.switch_page(Page.SETTINGS)
        elif event.key == pygame.K_a:
            # Deliberate activation: the AI workspace only ever appears when
            # the user asks for it.
            self.switch_page(Page.AI)
        elif event.key == pygame.K_v:
            # The microphone has its own key so it can be used without opening
            # the AI page - and it never opens by itself.
            self._handle_voice_toggle()
        elif event.key == pygame.K_c:
            # Toggle mouse control from the interface (never from the hand,
            # which can only pause an already armed controller).
            if self.on_control_toggle:
                self.on_control_toggle()
        elif event.key == pygame.K_m:
            # Control modes are only ever changed deliberately.
            self._run_token("mode:MOUSE")
        elif event.key == pygame.K_d:
            self._run_token("mode:DEVICE")
        elif event.key == pygame.K_p:
            # Diagnostics can be hidden so the camera view stays clean.
            self.telemetry.diagnostics_visible = not self.telemetry.diagnostics_visible
        elif event.key == pygame.K_r:
            if self.telemetry.app_state == AppState.CAMERA_ERROR:
                self._run_token("retry_camera")
        return True

    def _handle_click(self, position: Tuple[int, int]) -> None:
        """Route a click to the shell, then to the active page."""
        for page, rect in self._nav_rects:
            if rect.collidepoint(position):
                self.switch_page(page)
                return

        if self._banner_recover_rect is not None and self._banner_recover_rect.collidepoint(position):
            self._run_token("recover")
            return

        page = self._pages[self.page]
        handler = getattr(page, "handle_click", None)
        token = handler(position) if handler else None
        if token:
            self._run_token(token)

    def _run_ai_intent(self, intent: str) -> None:
        """Run one AI workspace intent."""
        if intent == "close":
            self.switch_page(Page.VISION)
        elif intent == "clear":
            self.ai_page.clear_input()
            if self.on_ai_clear:
                self.on_ai_clear()
        elif intent == "send":
            self._send_ai_input()
        elif intent == "microphone":
            self._handle_voice_toggle()
        elif intent == "confirm":
            if self.on_ai_confirm:
                self.on_ai_confirm()
        elif intent == "cancel":
            if self.on_ai_cancel:
                self.on_ai_cancel()

    def _send_ai_input(self) -> None:
        """Send the composer's text through the existing AI pathway."""
        text = self.ai_page.consume_input()
        if not text:
            return
        if self.on_ai_send and not self.on_ai_send(text):
            # A request is already in flight: keep what the user typed
            # instead of silently discarding it.
            self.ai_page.input_text = text[:600]

    def _handle_voice_toggle(self) -> None:
        """Ask the application to start or cancel listening."""
        if self.on_voice_toggle:
            self.on_voice_toggle()

    # ------------------------------------------------------------------ #
    # Token routing: page controls -> application callbacks
    # ------------------------------------------------------------------ #

    def _run_token(self, token: str) -> None:
        """Map one page token onto the existing application pathways."""
        if token == "toggle":
            # The primary control action for the active mode.
            if self.telemetry.control_mode is ControlMode.DEVICE:
                if self.on_device_toggle:
                    self.on_device_toggle()
            elif self.on_control_toggle:
                self.on_control_toggle()
        elif token == "disable":
            if self.on_control_disable:
                self.on_control_disable()
        elif token == "device_toggle":
            if self.on_device_toggle:
                self.on_device_toggle()
        elif token.startswith("mode:"):
            mode = ControlMode.DEVICE if token.endswith("DEVICE") else ControlMode.MOUSE
            if self.on_control_mode:
                self.on_control_mode(mode)
        elif token.startswith("device:"):
            try:
                action = DeviceAction(token.split(":", 1)[1])
            except ValueError:
                return
            if self.on_device_action:
                self.on_device_action(action, None)
        elif token.startswith("launch:"):
            if self.on_device_action:
                self.on_device_action(DeviceAction.LAUNCH_APP, token.split(":", 1)[1])
        elif token == "emergency_stop":
            # The existing highest-priority route: release and stop both
            # control layers, exactly as the stop gesture does.
            if self.on_emergency_stop:
                self.on_emergency_stop()
        elif token == "recover":
            # Deliberate recovery through the existing toggle paths, per layer.
            if self.telemetry.control_state is ControlState.EMERGENCY_STOP:
                if self.on_control_toggle:
                    self.on_control_toggle()
            if self.telemetry.device_state is ControlState.EMERGENCY_STOP:
                if self.on_device_toggle:
                    self.on_device_toggle()
        elif token == "viewport_retry":
            if self.on_recovery:
                self.on_recovery(self.vision_page.take_recovery())
        elif token == "retry_camera":
            logger.info("User requested camera reconnection retry")
            if self.on_retry_camera:
                self.on_retry_camera()
        elif token == "open_settings":
            self.switch_page(Page.SETTINGS)
        elif token == "exit":
            logger.info("User initiated exit from UI")
            self.is_running = False
            if self.on_exit:
                self.on_exit()
        elif token == "toggle_diagnostics":
            self.telemetry.diagnostics_visible = not self.telemetry.diagnostics_visible
        elif token == "toggle_fullscreen":
            self._toggle_fullscreen()
        elif token == "send":
            self._send_ai_input()
        elif token == "microphone":
            self._handle_voice_toggle()
        elif token == "confirm":
            if self.on_ai_confirm:
                self.on_ai_confirm()
        elif token == "cancel":
            if self.on_ai_cancel:
                self.on_ai_cancel()
        elif token.startswith("suggest:"):
            text = token.split(":", 1)[1]
            if self.on_ai_send and not self.on_ai_send(text):
                self.ai_page.input_text = text[:600]

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def render_frame(
        self,
        frame,
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
        control,
        device,
    ) -> None:
        """Dispatch rendering based on the current application state."""
        self.pulse.update(dt)
        self._transition = min(1.0, self._transition + dt / TRANSITION_DURATION)
        self.vision_page.update(dt, tracking, gesture)

        state = self.telemetry.app_state

        if state == AppState.BOOTING:
            # The sequence is advanced once per frame by the application loop;
            # rendering must never step it a second time.
            self.boot_screen.render(
                self.surface, pygame.Rect(0, 0, self.width, self.height), self.fonts
            )
        else:
            self._render_shell(frame, tracking, gesture)

        pygame.display.flip()

    def _content_rect(self) -> pygame.Rect:
        """The page area: right of the rail, below the top bar."""
        return pygame.Rect(
            NAV_WIDTH + CONTENT_PAD_X,
            TOP_BAR_HEIGHT + CONTENT_PAD_TOP,
            max(200, self.width - NAV_WIDTH - 2 * CONTENT_PAD_X),
            max(120, self.height - TOP_BAR_HEIGHT - CONTENT_PAD_TOP - CONTENT_PAD_BOTTOM),
        )

    def _render_shell(self, frame, tracking: TrackingSnapshot, gesture: GestureSnapshot) -> None:
        """Top bar, navigation rail, safety banner and the active page."""
        self.surface.fill(COLOR_BG)

        emergency = (
            self.telemetry.control_state is ControlState.EMERGENCY_STOP
            or self.telemetry.device_state is ControlState.EMERGENCY_STOP
        )

        self._draw_top_bar(pygame.Rect(0, 0, self.width, TOP_BAR_HEIGHT))
        self._draw_nav_rail(pygame.Rect(0, TOP_BAR_HEIGHT, NAV_WIDTH, self.height - TOP_BAR_HEIGHT))

        self.telemetry.ai_panel_visible = self.page is Page.AI

        content = self._content_rect()
        if emergency:
            banner = pygame.Rect(content.left, content.top, content.width, BANNER_HEIGHT)
            self._draw_safety_banner(banner)
            content = pygame.Rect(
                content.left, content.top + BANNER_HEIGHT + 10,
                content.width, max(120, content.height - BANNER_HEIGHT - 10),
            )

        # Page transition: a short rise inside the content clip.
        previous_clip = self.surface.get_clip()
        self.surface.set_clip(content)
        try:
            rise = int(TRANSITION_RISE * (1.0 - ease_out_cubic(self._transition)))
            page_rect = content.copy()
            page_rect.y += rise
            page_rect.height = max(120, page_rect.height - rise)
            self._render_page(page_rect, frame, tracking, gesture)
            if self._transition < 1.0:
                veil = pygame.Surface(content.size, pygame.SRCALPHA)
                veil.fill((*COLOR_BG, int(110 * (1.0 - ease_out_cubic(self._transition)))))
                self.surface.blit(veil, content.topleft)
        finally:
            self.surface.set_clip(previous_clip)

    def _render_page(
        self,
        rect: pygame.Rect,
        frame,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
    ) -> None:
        telemetry = self.telemetry
        if self.page is Page.VISION:
            self.vision_page.render(
                self.surface, rect, frame, telemetry, self.fonts, tracking, gesture
            )
        elif self.page is Page.AI:
            self.ai_page.render(self.surface, rect, telemetry, self.fonts)
        elif self.page is Page.CONTROLS:
            self.controls_page.render(self.surface, rect, telemetry, self.fonts)
        else:
            self.settings_page.window_size = (self.width, self.height)
            self.settings_page.render(self.surface, rect, telemetry, self.fonts)

    # -- top bar ---------------------------------------------------------- #

    def _draw_top_bar(self, rect: pygame.Rect) -> None:
        """Wordmark on the left, live status indicators on the right."""
        pygame.draw.rect(self.surface, COLOR_BG, rect)
        pygame.draw.line(
            self.surface, COLOR_BORDER,
            (rect.left, rect.bottom - 1), (rect.right, rect.bottom - 1), 1,
        )

        # Wordmark.
        mark = icons.icon("eye", 18, COLOR_ACCENT)
        self.surface.blit(mark, (rect.left + 20, rect.centery - mark.get_height() // 2))
        name = self.fonts["heading"].render("VisionCore", True, COLOR_TEXT)
        self.surface.blit(
            name, (rect.left + 20 + mark.get_width() + 9, rect.centery - name.get_height() // 2 - 1)
        )
        version = self.fonts["small"].render(f"v{VERSION}", True, COLOR_TEXT_FAINT)
        self.surface.blit(
            version,
            (rect.left + 20 + mark.get_width() + 9 + name.get_width() + 8,
             rect.centery - version.get_height() // 2),
        )

        # Status indicators, newest priority first, dropped as the window
        # narrows so they never collide with the wordmark.
        items = self._status_items()
        x = rect.right - 20
        brand_end = (
            rect.left + 20 + mark.get_width() + 9 + name.get_width()
            + 8 + version.get_width() + 28
        )
        for label, value, color in items:
            item_width = self._status_item_width(label, value)
            if x - item_width < brand_end:
                break
            self._draw_status_item(x - item_width, rect.centery, label, value, color)
            x -= item_width + 22

    def _status_items(self) -> List[Tuple[str, str, Tuple[int, int, int]]]:
        """(label, value, colour) for each indicator, highest priority first.

        The items are drawn right to left, so when the window narrows the least
        important ones (voice, AI, mode, tracking) are the first to be dropped.
        """
        telemetry = self.telemetry

        # Camera: the application lifecycle is the truth here.
        if telemetry.app_state is AppState.CAMERA_ACTIVE:
            camera = ("Camera", "Active", subsystem_status_color(telemetry.camera))
        elif telemetry.app_state is AppState.CAMERA_ERROR:
            camera = ("Camera", "Unavailable", COLOR_DANGER)
        else:
            camera = ("Camera", "Starting", COLOR_TEXT_FAINT)

        # Tracking: the tracker's own state.
        if telemetry.tracking is SubsystemState.DISABLED:
            tracking = ("Tracking", "Off", COLOR_TEXT_FAINT)
        elif telemetry.tracking in (SubsystemState.UNAVAILABLE, SubsystemState.ERROR):
            tracking = ("Tracking", "Unavailable", COLOR_DANGER)
        else:
            tracking = (
                "Tracking",
                telemetry.tracking_state.status_label.capitalize(),
                subsystem_status_color(telemetry.tracking),
            )

        mode = ("Mode", telemetry.control_mode.label, COLOR_ACCENT)
        ai = ("AI", telemetry.ai.status_label, ai_status_color(telemetry.ai.status))
        voice = ("Voice", telemetry.voice.status_label, voice_state_color(telemetry.voice.state))
        return [camera, tracking, mode, ai, voice]

    def _status_item_width(self, label: str, value: str) -> int:
        label_w = self.fonts["small"].size(label)[0]
        value_w = self.fonts["small"].size(value)[0]
        return 8 + 6 + label_w + 5 + value_w

    def _draw_status_item(
        self,
        x: int,
        center_y: int,
        label: str,
        value: str,
        color: Tuple[int, int, int],
    ) -> None:
        """One indicator: status dot, quiet label, real value."""
        dot_color = color
        if color is COLOR_SUCCESS or color is COLOR_WARNING:
            dot_color = tuple(
                int(channel * (0.55 + 0.45 * self.pulse.value)) for channel in color
            )
        pygame.draw.circle(self.surface, dot_color, (int(x + 4), center_y), 3)
        label_surf = self.fonts["small"].render(label, True, COLOR_TEXT_FAINT)
        value_surf = self.fonts["small"].render(value, True, COLOR_TEXT_DIM)
        self.surface.blit(label_surf, (x + 14, center_y - label_surf.get_height() // 2))
        self.surface.blit(
            value_surf,
            (x + 14 + label_surf.get_width() + 5, center_y - value_surf.get_height() // 2),
        )

    # -- navigation rail -------------------------------------------------- #

    def _draw_nav_rail(self, rect: pygame.Rect) -> None:
        """Compact navigation: outline icon, small label, quiet active state."""
        pygame.draw.rect(self.surface, COLOR_BG, rect)
        pygame.draw.line(
            self.surface, COLOR_BORDER,
            (rect.right - 1, rect.top), (rect.right - 1, rect.bottom), 1,
        )

        mouse = pygame.mouse.get_pos()
        self._nav_rects = []
        self._nav_hover = None
        y = rect.top + 14
        item_height = 60

        for page_value, label, icon_name, shortcut in NAV_ITEMS:
            page = Page(page_value)
            item = pygame.Rect(rect.centerx - (rect.width - 16) // 2, y,
                               rect.width - 16, item_height)
            self._nav_rects.append((page, item))
            hovered = item.collidepoint(mouse)
            if hovered:
                self._nav_hover = page
            active = page is self.page

            if active:
                pygame.draw.rect(self.surface, COLOR_ACCENT_TINT, item, border_radius=12)
                pygame.draw.rect(self.surface, (44, 62, 76), item, 1, border_radius=12)
                icon_color = COLOR_ACCENT
                label_color = COLOR_TEXT
            elif hovered:
                pygame.draw.rect(self.surface, (20, 22, 26), item, border_radius=12)
                icon_color = COLOR_TEXT_DIM
                label_color = COLOR_TEXT_DIM
            else:
                icon_color = COLOR_TEXT_FAINT
                label_color = COLOR_TEXT_FAINT

            mark = icons.icon(icon_name, 22, icon_color)
            self.surface.blit(
                mark, (item.centerx - mark.get_width() // 2, item.top + 10)
            )
            text = self.fonts["small"].render(label, True, label_color)
            self.surface.blit(
                text, (item.centerx - text.get_width() // 2, item.bottom - 22)
            )
            y += item_height + 6

        # Tooltip for the hovered item, quietly showing its shortcut.
        if self._nav_hover is not None and self._nav_hover is not self.page:
            for page_value, label, icon_name, shortcut in NAV_ITEMS:
                if Page(page_value) is self._nav_hover:
                    tip = self.fonts["small"].render(
                        f"{label}  ·  {shortcut}", True, COLOR_TEXT_DIM
                    )
                    tip_rect = pygame.Rect(
                        rect.right + 8, rect.top + 8, tip.get_width() + 16, 26
                    )
                    draw_panel(self.surface, tip_rect, fill=(22, 24, 28),
                               border=COLOR_BORDER, radius=8)
                    self.surface.blit(
                        tip, (tip_rect.left + 8, tip_rect.centery - tip.get_height() // 2)
                    )
                    break

    # -- safety banner ---------------------------------------------------- #

    def _draw_safety_banner(self, rect: pygame.Rect) -> None:
        """The one element allowed to interrupt every page."""
        mouse = pygame.mouse.get_pos()
        draw_panel(self.surface, rect, fill=COLOR_DANGER_TINT, border=COLOR_DANGER, radius=12)

        mark = icons.icon("warning", 18, COLOR_DANGER)
        self.surface.blit(mark, (rect.left + 14, rect.centery - mark.get_height() // 2))
        message = fit(
            "Emergency stop active — control is stopped and latched. "
            "Recovery required.",
            self.fonts["body"], max(60, rect.width - 190),
        )
        text = self.fonts["body"].render(message, True, COLOR_TEXT)
        self.surface.blit(text, (rect.left + 40, rect.centery - text.get_height() // 2))

        recover = pygame.Rect(rect.right - 122, rect.centery - 16, 108, 32)
        draw_button(
            self.surface, recover, "Recover", self.fonts, kind="success",
            hovered=recover.collidepoint(mouse), icon="refresh", icon_module=icons,
        )
        self._banner_recover_rect = recover

    # -- shutdown --------------------------------------------------------- #

    def render_shutdown(self, dt: float) -> bool:
        """Render one frame of the shutdown sequence.

        Returns True once the sequence has finished. The application has
        already released control, stopped the tracker and closed the camera
        before this is called, so nothing here can delay a safety action.
        """
        finished = self.shutdown_screen.update(dt)
        self.shutdown_screen.render(
            self.surface, pygame.Rect(0, 0, self.width, self.height), self.fonts
        )
        pygame.display.flip()
        return finished

    def close(self) -> None:
        """Cleanly close the window and release its pygame resources.

        The font handles are dropped and the font module is uninitialised here
        so that a complete start/stop cycle leaves no descriptor open behind it;
        the application calls ``pygame.quit`` immediately afterwards.
        """
        self.ai_page.clear_input()
        logger.debug("Closing application window...")
        try:
            self.fonts = {}
            pygame.font.quit()
        except Exception as exc:
            logger.warning("Error releasing fonts: %s", exc)
        try:
            pygame.display.quit()
        except Exception as exc:
            logger.warning("Error quitting display: %s", exc)
