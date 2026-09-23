"""Main application desktop window, event loop, and layout manager."""

from __future__ import annotations

import time

import logging
import os
import shutil
import warnings
from typing import Callable, Dict, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.config import AppConfig
from app.controls import ControlMode, ControlSnapshot, DeviceAction, DeviceSnapshot
from app.gestures import GestureSnapshot
from app.hand_tracking import TrackingSnapshot
from app.interaction import RecoveryAction
from app.state import AppState, Telemetry

# A sidebar module never collapses below this, even in a very short window.
MIN_PANEL_HEIGHT = 48
from ui.ai_panel import AIPanel
from ui.animations import PulseAnimation
from ui.boot_screen import BootScreen
from ui.camera_view import CameraView
from ui.shutdown_screen import ShutdownScreen
from ui.hud import (
    COLOR_BG_DARK,
    COLOR_CYAN_PRIMARY,
    COLOR_ERROR,
    COLOR_ICE_BLUE,
    COLOR_PANEL_BG,
    COLOR_PANEL_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
    HUDManager,
)

logger = logging.getLogger("visioncore.ui")


class UIButton:
    """Futuristic interactive button with hover states and click detection."""

    def __init__(
        self,
        rect: pygame.Rect,
        text: str,
        is_primary: bool = False,
        callback: Optional[Callable[[], None]] = None,
    ):
        self.rect = rect
        self.text = text
        self.is_primary = is_primary
        self.callback = callback
        self.is_hovered = False

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Process mouse motion and click events. Returns True if clicked."""
        if event.type == pygame.MOUSEMOTION:
            self.is_hovered = self.rect.collidepoint(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                if self.callback:
                    self.callback()
                return True
        return False

    def render(self, surface: pygame.Surface, fonts: Dict[str, pygame.font.Font]) -> None:
        """Render the button with glowing hover accents."""
        # Background
        if self.is_hovered:
            bg_color = (20, 36, 56) if self.is_primary else (28, 40, 56)
            border_color = COLOR_CYAN_PRIMARY if self.is_primary else COLOR_ICE_BLUE
            text_color = COLOR_TEXT_WHITE
        else:
            bg_color = (12, 22, 34) if self.is_primary else (15, 20, 30)
            border_color = (0, 180, 216) if self.is_primary else COLOR_PANEL_BORDER
            text_color = COLOR_CYAN_PRIMARY if self.is_primary else COLOR_TEXT_MUTED

        pygame.draw.rect(surface, bg_color, self.rect)
        pygame.draw.rect(surface, border_color, self.rect, 1)

        # Subtle corner tick marks on hover
        if self.is_hovered:
            x, y, w, h = self.rect.x, self.rect.y, self.rect.width, self.rect.height
            pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x, y), (x + 8, y), 2)
            pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x, y), (x, y + 8), 2)
            pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x + w, y + h), (x + w - 8, y + h), 2)
            pygame.draw.line(surface, COLOR_CYAN_PRIMARY, (x + w, y + h), (x + w, y + h - 8), 2)

        txt_surf = fonts["body"].render(self.text, True, text_color)
        txt_rect = txt_surf.get_rect(center=self.rect.center)
        surface.blit(txt_surf, txt_rect)


class MainWindow:
    """Desktop window host, rendering coordinator, and input event router."""

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

        self.width = max(config.min_window_width, config.window_width)
        self.height = max(config.min_window_height, config.window_height)
        self.is_running = False

        # Initialize Pygame display subsystem
        if not pygame.get_init():
            pygame.init()
        if not pygame.font.get_init():
            pygame.font.init()

        # Window flags
        flags = pygame.RESIZABLE
        if config.fullscreen:
            flags |= pygame.FULLSCREEN

        self.surface = pygame.display.set_mode((self.width, self.height), flags)
        pygame.display.set_caption(config.window_title)

        self.clock = pygame.time.Clock()
        self.fonts = self._init_fonts()

        # UI Subcomponents
        self.boot_screen = BootScreen(duration_sec=config.boot_duration_sec)
        self.shutdown_screen = ShutdownScreen()
        self.hud_manager = HUDManager()
        self.camera_view = CameraView(hud=self.hud_manager)
        self.pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.0)

        # VisionCore AI panel: opened deliberately (HUD button or the A key),
        # never by a gesture and never by itself.
        self.ai_panel = AIPanel()
        self.ai_panel_open = False
        self.ai_button_rect: Optional[pygame.Rect] = None

        # Voice timing for the HUD: elapsed is measured against one time base
        # (the session clock), never against the render loop's own rate.
        self._session_started = time.perf_counter()

        # Interactive error recovery buttons
        self.btn_retry: Optional[UIButton] = None
        self.btn_exit: Optional[UIButton] = None
        self._update_error_buttons()

        # Sidebar control rectangles, refreshed every rendered frame so clicks
        # always land on the button the user can actually see.
        self.control_toggle_rect: Optional[pygame.Rect] = None
        self.control_disable_rect: Optional[pygame.Rect] = None
        self.device_buttons: Dict[str, Optional[pygame.Rect]] = {}
        # Recovery button offered inside the viewport when an error state has a
        # genuine retry; the window owns the click routing.
        self.recovery_button: Optional[Tuple[RecoveryAction, pygame.Rect]] = None

    def _init_fonts(self) -> Dict[str, pygame.font.Font]:
        """Initialize clean, platform-independent typography."""
        has_fontconfig = bool(shutil.which("fc-list"))

        def get_font(size: int) -> pygame.font.Font:
            if has_fontconfig:
                candidates = ["segoeui", "helvetica", "dejavusans", "arial"]
                for name in candidates:
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            f = pygame.font.SysFont(name, size)
                            if f:
                                return f
                    except Exception:
                        pass
            return pygame.font.Font(None, size)

        return {
            "title": get_font(26),
            "subheading": get_font(18),
            "body": get_font(15),
            "caption": get_font(13),
            "mono": get_font(14),
            "mono_small": get_font(12),
        }

    def _update_error_buttons(self) -> None:
        """Position the Retry and Exit buttons responsively in the center."""
        card_w = min(680, self.width - 40)
        card_x = self.width // 2 - card_w // 2
        card_y = self.height // 2 - 200

        btn_y = card_y + 330
        btn_w = 170
        btn_h = 38

        retry_x = card_x + (card_w // 2) - btn_w - 15
        exit_x = card_x + (card_w // 2) + 15

        self.btn_retry = UIButton(
            rect=pygame.Rect(retry_x, btn_y, btn_w, btn_h),
            text="[ RETRY CAMERA ]",
            is_primary=True,
            callback=self._handle_retry,
        )
        self.btn_exit = UIButton(
            rect=pygame.Rect(exit_x, btn_y, btn_w, btn_h),
            text="[ EXIT SYSTEM ]",
            is_primary=False,
            callback=self._handle_exit,
        )

    def _handle_retry(self) -> None:
        """Trigger camera re-probe and restart."""
        logger.info("User requested camera reconnection retry")
        if self.on_retry_camera:
            self.on_retry_camera()

    def _handle_exit(self) -> None:
        """Trigger graceful application exit."""
        logger.info("User initiated exit from UI")
        self.is_running = False
        if self.on_exit:
            self.on_exit()

    def handle_events(self) -> bool:
        """
        Poll and handle window events (close, resize, keyboard, mouse).
        Returns False when application should terminate.
        """
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                logger.info("Window close event received")
                return False

            elif event.type == pygame.VIDEORESIZE:
                # Clamp to minimum dimensions
                new_w = max(self.config.min_window_width, event.w)
                new_h = max(self.config.min_window_height, event.h)
                self.width = new_w
                self.height = new_h
                self.surface = pygame.display.set_mode(
                    (new_w, new_h), pygame.RESIZABLE
                )
                self._update_error_buttons()
                logger.debug("Window resized to %dx%d", new_w, new_h)

            elif event.type == pygame.KEYDOWN and self.ai_panel_open:
                # While the assistant panel is focused every keystroke belongs to
                # it, so a typed "d" can never switch control modes.
                if event.key == pygame.K_ESCAPE:
                    logger.info("Escape key pressed -> quitting")
                    return False
                if event.key == pygame.K_F11:
                    pass
                elif event.key == pygame.K_a and not self.ai_panel.input_text:
                    self.toggle_ai_panel(False)
                elif event.key == pygame.K_v and not self.ai_panel.input_text:
                    # Deliberate microphone activation, either way: V opens the
                    # microphone, and V again cancels what is being listened to.
                    # While a message is being typed "v" is just a letter.
                    self._handle_voice_toggle()
                else:
                    intent = self.ai_panel.handle_key(event)
                    if intent is not None:
                        self._handle_ai_intent(intent)

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    logger.info("Escape key pressed -> quitting")
                    return False
                elif event.key == pygame.K_F11:
                    # Toggle fullscreen
                    self.config.fullscreen = not self.config.fullscreen
                    flags = pygame.FULLSCREEN if self.config.fullscreen else pygame.RESIZABLE
                    self.surface = pygame.display.set_mode((self.width, self.height), flags)
                    self._update_error_buttons()
                elif event.key == pygame.K_r:
                    if self.telemetry.app_state == AppState.CAMERA_ERROR:
                        self._handle_retry()
                elif event.key == pygame.K_c:
                    # Toggle mouse control from the interface (never from the hand,
                    # which can only pause an already armed controller).
                    self._handle_control_toggle()
                elif event.key == pygame.K_m:
                    # Control modes are only ever changed deliberately.
                    self._handle_control_mode(ControlMode.MOUSE)
                elif event.key == pygame.K_d:
                    self._handle_control_mode(ControlMode.DEVICE)
                elif event.key == pygame.K_p:
                    # Diagnostics can be hidden so the normal view stays clean.
                    self.telemetry.diagnostics_visible = not self.telemetry.diagnostics_visible
                elif event.key == pygame.K_a:
                    # Deliberate activation: the assistant only ever appears when
                    # the user asks for it.
                    self.toggle_ai_panel(True)
                elif event.key == pygame.K_v:
                    # The microphone has its own key so it can be used without
                    # opening the panel - and it never opens by itself.
                    self._handle_voice_toggle()

            elif event.type == pygame.MOUSEWHEEL:
                if self.ai_panel_open:
                    self.ai_panel.handle_wheel(event.y)

            # Control buttons live in the sidebar telemetry panels.
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self._handle_ai_click(event.pos):
                    continue
                if self._handle_recovery_click(event.pos):
                    continue
                if self._handle_control_click(event.pos):
                    continue
                if self._handle_device_click(event.pos):
                    continue
                if self.ai_button_rect and self.ai_button_rect.collidepoint(event.pos):
                    self.toggle_ai_panel(True)
                    continue

            # Handle interactive buttons if in error state
            if self.telemetry.app_state == AppState.CAMERA_ERROR:
                if self.btn_retry and self.btn_retry.handle_event(event):
                    pass
                if self.btn_exit and self.btn_exit.handle_event(event):
                    return False

        return True

    # -- assistant panel --------------------------------------------------- #

    def toggle_ai_panel(self, open_: Optional[bool] = None) -> bool:
        """Open or close the assistant panel (explicit user action only)."""
        self.ai_panel_open = not self.ai_panel_open if open_ is None else bool(open_)
        self.telemetry.ai_panel_visible = self.ai_panel_open
        if not self.ai_panel_open:
            self.ai_panel.clear_input()
        logger.info("VisionCore AI panel %s", "opened" if self.ai_panel_open else "closed")
        return self.ai_panel_open

    def _handle_ai_click(self, position: Tuple[int, int]) -> bool:
        """Route a click to the panel when it is open. True when consumed."""
        if not self.ai_panel_open:
            return False
        intent = self.ai_panel.handle_click(position)
        return self._handle_ai_intent(intent) if intent else False

    def _handle_ai_intent(self, intent: str) -> bool:
        """Run one panel intent. Returns True when the event is consumed."""
        if intent == "close":
            self.toggle_ai_panel(False)
            return True
        if intent == "clear":
            self.ai_panel.clear_input()
            if self.on_ai_clear:
                self.on_ai_clear()
            return True
        if intent == "focus":
            return self.ai_panel.contains(pygame.mouse.get_pos())
        if intent == "send":
            text = self.ai_panel.consume_input()
            if not text:
                return True
            if self.on_ai_send and not self.on_ai_send(text):
                # A request is already in flight: keep what the user typed
                # instead of silently discarding it.
                self.ai_panel.input_text = text[:600]
            return True
        if intent == "microphone":
            self._handle_voice_toggle()
            return True
        if intent in ("confirm", "cancel"):
            # Confirmation is performed by the application, never here: the panel
            # only reports that the user pressed the button.
            callback = self.on_ai_confirm if intent == "confirm" else self.on_ai_cancel
            if callback:
                callback()
            return True
        return True

    def _handle_voice_toggle(self) -> None:
        """Ask the application to start or cancel listening (never optional here)."""
        if self.on_voice_toggle:
            self.on_voice_toggle()

    def _handle_recovery_click(self, position: Tuple[int, int]) -> bool:
        """Route a click on the viewport RETRY button. True when consumed."""
        if self.recovery_button is None:
            return False
        recovery, rect = self.recovery_button
        if not rect.collidepoint(position):
            return False
        if self.on_recovery:
            self.on_recovery(recovery)
        return True

    def _handle_control_click(self, position: Tuple[int, int]) -> bool:
        """Route a click to the sidebar control buttons. True when consumed."""
        if self.control_disable_rect and self.control_disable_rect.collidepoint(position):
            if self.on_control_disable:
                self.on_control_disable()
            return True
        if self.control_toggle_rect and self.control_toggle_rect.collidepoint(position):
            self._handle_control_toggle()
            return True
        return False

    def _handle_control_toggle(self) -> None:
        if self.on_control_toggle:
            self.on_control_toggle()

    def _handle_control_mode(self, mode: ControlMode) -> None:
        if self.on_control_mode:
            self.on_control_mode(mode)

    def _handle_device_click(self, position: Tuple[int, int]) -> bool:
        """Route a click to the device module buttons. True when consumed."""
        for role, rect in self.device_buttons.items():
            if rect is None or not rect.collidepoint(position):
                continue
            if role == "mode":
                mode = (
                    ControlMode.MOUSE
                    if self.telemetry.control_mode is ControlMode.DEVICE
                    else ControlMode.DEVICE
                )
                self._handle_control_mode(mode)
            elif role == "toggle":
                if self.on_device_toggle:
                    self.on_device_toggle()
            elif role == "minimize":
                self._device_action(DeviceAction.MINIMIZE)
            elif role == "maximize":
                self._device_action(DeviceAction.MAXIMIZE)
            elif role == "switch":
                self._device_action(DeviceAction.NEXT_WINDOW)
            elif role.startswith("launch:"):
                self._device_action(DeviceAction.LAUNCH_APP, role.split(":", 1)[1])
            return True
        return False

    def _device_action(self, action: DeviceAction, argument: Optional[str] = None) -> None:
        if self.on_device_action:
            self.on_device_action(action, argument)

    def render_error_screen(self) -> None:
        """Render polished sci-fi camera unavailable recovery screen."""
        self.surface.fill(COLOR_BG_DARK)

        card_w = min(680, self.width - 40)
        card_h = 400
        card_x = self.width // 2 - card_w // 2
        card_y = self.height // 2 - card_h // 2
        card_rect = pygame.Rect(card_x, card_y, card_w, card_h)

        # Panel body & border
        pygame.draw.rect(self.surface, COLOR_PANEL_BG, card_rect)
        pygame.draw.rect(self.surface, (50, 25, 35), card_rect, 1)

        # Corner warning brackets
        b_len = 22
        b_col = COLOR_ERROR
        pygame.draw.line(self.surface, b_col, (card_x, card_y), (card_x + b_len, card_y), 2)
        pygame.draw.line(self.surface, b_col, (card_x, card_y), (card_x, card_y + b_len), 2)
        pygame.draw.line(self.surface, b_col, (card_x + card_w, card_y), (card_x + card_w - b_len, card_y), 2)
        pygame.draw.line(self.surface, b_col, (card_x + card_w, card_y), (card_x + card_w, card_y + b_len), 2)
        pygame.draw.line(self.surface, b_col, (card_x, card_y + card_h), (card_x + b_len, card_y + card_h), 2)
        pygame.draw.line(self.surface, b_col, (card_x, card_y + card_h), (card_x, card_y + card_h - b_len), 2)
        pygame.draw.line(self.surface, b_col, (card_x + card_w, card_y + card_h), (card_x + card_w - b_len, card_y + card_h), 2)
        pygame.draw.line(self.surface, b_col, (card_x + card_w, card_y + card_h), (card_x + card_w, card_y + card_h - b_len), 2)

        # Title
        title_surf = self.fonts["title"].render("VISIONCORE", True, COLOR_TEXT_WHITE)
        title_rect = title_surf.get_rect(center=(card_rect.centerx, card_y + 36))
        self.surface.blit(title_surf, title_rect)

        # Pulsing Error Banner
        error_surf = self.fonts["subheading"].render("CAMERA HARDWARE UNAVAILABLE", True, COLOR_ERROR)
        error_rect = error_surf.get_rect(center=(card_rect.centerx, card_y + 70))
        self.surface.blit(error_surf, error_rect)

        pygame.draw.line(
            self.surface,
            COLOR_PANEL_BORDER,
            (card_x + 30, card_y + 96),
            (card_x + card_w - 30, card_y + 96),
            1,
        )

        # Primary explanatory message
        msg = self.telemetry.error_message or "No usable camera device could be initialized."
        msg_surf = self.fonts["body"].render(msg, True, COLOR_TEXT_WHITE)
        msg_rect = msg_surf.get_rect(center=(card_rect.centerx, card_y + 124))
        self.surface.blit(msg_surf, msg_rect)

        # Troubleshooting checklist header
        chk_header = self.fonts["caption"].render("CHECK THAT:", True, COLOR_CYAN_PRIMARY)
        self.surface.blit(chk_header, (card_x + 45, card_y + 158))

        # Checklist bullets
        instructions = self.telemetry.error_instructions or [
            "Your camera is physically connected and turned on.",
            "Operating system camera permissions are enabled for Python.",
            "Another application is not exclusively locking the camera device.",
        ]

        y_bullet = card_y + 184
        for instr in instructions:
            pygame.draw.circle(self.surface, COLOR_CYAN_PRIMARY, (card_x + 55, y_bullet + 7), 3)
            bullet_surf = self.fonts["caption"].render(instr, True, COLOR_TEXT_MUTED)
            self.surface.blit(bullet_surf, (card_x + 68, y_bullet))
            y_bullet += 24

        # Render interactive buttons
        if self.btn_retry:
            self.btn_retry.render(self.surface, self.fonts)
        if self.btn_exit:
            self.btn_exit.render(self.surface, self.fonts)

        # Footer shortcut hint
        hint_surf = self.fonts["mono_small"].render(
            "PRESS [R] TO RETRY  |  [ESC] TO EXIT",
            True,
            COLOR_TEXT_MUTED,
        )
        hint_rect = hint_surf.get_rect(center=(card_rect.centerx, card_y + card_h - 18))
        self.surface.blit(hint_surf, hint_rect)

    def render_active_hud(
        self,
        frame: Optional[any],
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
        control: ControlSnapshot,
        device: DeviceSnapshot,
    ) -> None:
        """Render header, camera viewport, telemetry sidebars, and footer."""
        self.surface.fill(COLOR_BG_DARK)

        # 1. Top Header Bar
        header_rect = pygame.Rect(0, 0, self.width, 58)
        self.hud_manager.draw_header(self.surface, header_rect, self.telemetry, self.fonts)

        # 2. Bottom Diagnostics Footer
        footer_rect = pygame.Rect(0, self.height - 28, self.width, 28)
        self.hud_manager.draw_footer_diagnostics(self.surface, footer_rect, self.telemetry, self.fonts)

        # 3. Main Workspace Layout
        content_top = 68
        content_bottom = self.height - 38
        content_height = max(100, content_bottom - content_top)

        # The sidebar narrows with the window so the video area keeps a usable
        # size instead of being squeezed to a strip.
        sidebar_width = max(232, min(280, int(self.width * 0.28)))
        sidebar_x = self.width - sidebar_width - 16
        viewport_width = max(200, sidebar_x - 32)
        viewport_rect = pygame.Rect(16, content_top, viewport_width, content_height)

        # Render Video Viewport with HUD overlay. The camera view returns the
        # RETRY button rectangle when an error with a real recovery is shown.
        self.recovery_button = self.camera_view.render(
            self.surface,
            viewport_rect,
            frame,
            self.telemetry,
            self.fonts,
            tracking,
            gesture,
        )

        # 4. Telemetry Panels in Sidebar (stacked modules)
        panel_gap = 10
        rects = self._stack_panels(
            sidebar_x, sidebar_width, content_top, content_height, panel_gap
        )

        # Modules are drawn only if the layout kept them: a window too short for
        # the full stack shows fewer modules, never clipped ones.
        self.hud_manager.draw_system_matrix_panel(
            self.surface, rects["matrix"], self.telemetry, self.fonts
        )
        if "tracking" in rects:
            self._draw_panel_clipped(self.hud_manager.draw_tracking_panel, rects["tracking"])
        if "gesture" in rects:
            self._draw_panel_clipped(self.hud_manager.draw_gesture_panel, rects["gesture"])

        self.control_toggle_rect = None
        self.control_disable_rect = None
        if "control" in rects:
            self.surface.set_clip(rects["control"])
            try:
                (
                    self.control_toggle_rect,
                    self.control_disable_rect,
                ) = self.hud_manager.draw_control_panel(
                    self.surface, rects["control"], self.telemetry, self.fonts
                )
            finally:
                self.surface.set_clip(None)

        self.device_buttons = {}
        if "device" in rects:
            self.surface.set_clip(rects["device"])
            try:
                self.device_buttons = self.hud_manager.draw_device_panel(
                    self.surface, rects["device"], self.telemetry, self.fonts
                )
            finally:
                self.surface.set_clip(None)

        if "recent" in rects:
            self._draw_panel_clipped(
                self.camera_view.feedback_view.render_timeline_panel, rects["recent"]
            )
        if "camera" in rects:
            self._draw_panel_clipped(
                self.hud_manager.draw_camera_status_panel, rects["camera"]
            )

    def _draw_panel_clipped(self, draw, rect: pygame.Rect) -> None:
        """Draw one sidebar module inside its own rectangle.

        The clip keeps modules from ever writing over each other, so a window too
        short for every module shows less of a panel instead of a collision.
        """
        self.surface.set_clip(rect)
        try:
            draw(self.surface, rect, self.telemetry, self.fonts)
        finally:
            self.surface.set_clip(None)

    def _stack_panels(
        self,
        x: int,
        width: int,
        top: int,
        height: int,
        gap: int,
    ) -> Dict[str, pygame.Rect]:
        """Lay out the sidebar modules so nothing is ever clipped.

        Each module declares the height it needs to be fully readable and a floor
        it may shrink to. The matrix is treated as rigid because every row it
        carries is a status the user must be able to see; the other modules give
        up height proportionally, and their row renderers drop supplementary rows
        rather than overlapping.
        """
        # (name, ideal, floor, share of surplus). The matrix keeps its ideal
        # height because every row it carries is a status the user must see; the
        # modules below give up height proportionally and drop supplementary
        # rows rather than overlapping each other.
        specs = [
            # The matrix carries one row per subsystem, and since Phase 8 that
            # includes the microphone state, so it needs a sixth row.
            ("matrix", 140, 140, 0.18),
            ("tracking", 112, 104, 0.22),
            ("gesture", 84, 76, 0.15),
            ("control", 140, 130, 0.13),
            ("device", 190, 150, 0.20),
            ("recent", 100, 96, 0.12),
            ("camera", 70, 62, 0.12),
        ]
        # When the window is too short for every module, whole modules are
        # dropped in reverse order of importance rather than every panel being
        # crushed below the height it needs to be readable.
        for optional in ("camera", "recent", "gesture", "device", "control"):
            total = sum(floor for _, _, floor, _ in specs)
            if height - gap * (len(specs) - 1) >= total:
                break
            specs = [spec for spec in specs if spec[0] != optional]
        specs = tuple(specs)
        available = max(120, height - gap * (len(specs) - 1))
        total_ideal = sum(ideal for _, ideal, _, _ in specs)
        total_floor = sum(floor for _, _, floor, _ in specs)
        heights: Dict[str, float] = {}

        if available >= total_ideal:
            surplus = available - total_ideal
            for name, ideal, _floor, weight in specs:
                heights[name] = ideal + surplus * weight
        else:
            deficit = total_ideal - available
            shrinkable = total_ideal - total_floor
            if deficit <= shrinkable and shrinkable > 0:
                # Every module keeps at least its floor.
                scale = deficit / shrinkable
                for name, ideal, floor, _weight in specs:
                    heights[name] = ideal - (ideal - floor) * scale
            else:
                # The window is too short even for the floors: shrink all modules
                # together so the sidebar always ends inside the window.
                scale = available / total_floor
                for name, _ideal, floor, _weight in specs:
                    heights[name] = floor * scale

        # Integer heights that add up exactly and never collapse a module.
        pixels = {name: max(MIN_PANEL_HEIGHT, int(value)) for name, value in heights.items()}
        while sum(pixels.values()) > available:
            largest = max(pixels, key=lambda name: pixels[name])
            if pixels[largest] <= MIN_PANEL_HEIGHT:
                break
            pixels[largest] -= 1
        remainder = available - sum(pixels.values())
        if remainder > 0:
            widest = max(specs, key=lambda spec: spec[2])[0]
            pixels[widest] += remainder

        rects: Dict[str, pygame.Rect] = {}
        y = top
        for name, _ideal, _floor, _weight in specs:
            rects[name] = pygame.Rect(x, y, width, pixels[name])
            y += pixels[name] + gap
        return rects

    def render_frame(
        self,
        frame: Optional[any],
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
        control: ControlSnapshot,
        device: DeviceSnapshot,
    ) -> None:
        """Dispatch rendering based on current application state."""
        self.pulse.update(dt)
        self.hud_manager.update(dt)
        self.camera_view.update(dt, tracking, gesture)

        state = self.telemetry.app_state

        if state == AppState.BOOTING:
            # The sequence is advanced once per frame by the application loop;
            # rendering must never step it a second time.
            self.boot_screen.render(self.surface, pygame.Rect(0, 0, self.width, self.height), self.fonts)
        elif state == AppState.CAMERA_ACTIVE:
            self.render_active_hud(frame, tracking, gesture, control, device)
        elif state == AppState.CAMERA_ERROR:
            self.render_error_screen()

        # The assistant is an overlay: it can be opened in any state and it never
        # changes the layout the rest of the interface depends on.
        self.render_ai_panel()

        pygame.display.flip()

    def render_ai_panel(self) -> None:
        """Draw the assistant panel over the workspace when it is open."""
        self.ai_button_rect = self.hud_manager.draw_ai_button(
            self.surface,
            pygame.Rect(0, self.height - 28, self.width, 28),
            self.telemetry,
            self.fonts,
            opened=self.ai_panel_open,
        )
        if not self.ai_panel_open:
            return

        viewport = self._workspace_rect()
        content_height = max(100, self.height - 38 - 68)
        rect = self.ai_panel.layout(viewport, content_height)
        self.ai_panel.draw(
            self.surface,
            rect,
            self.telemetry.ai,
            self._ai_context_line(),
            self.fonts,
            voice=self.telemetry.voice,
        )

    def _workspace_rect(self) -> pygame.Rect:
        """The camera workspace, computed exactly as the HUD layout does."""
        content_top = 68
        content_bottom = self.height - 38
        content_height = max(100, content_bottom - content_top)
        sidebar_width = max(232, min(280, int(self.width * 0.28)))
        sidebar_x = self.width - sidebar_width - 16
        viewport_width = max(200, sidebar_x - 32)
        return pygame.Rect(16, content_top, viewport_width, content_height)

    def _ai_context_line(self) -> str:
        """One line of real state under the assistant status."""
        telemetry = self.telemetry
        mode = (
            telemetry.control_mode.value
            if hasattr(telemetry.control_mode, "value")
            else str(telemetry.control_mode)
        )
        line = (
            f"{mode} MODE | {telemetry.tracking_state.status_label} | "
            f"GESTURE {telemetry.gesture.value} | HANDS {telemetry.hands_detected}"
        )
        # The microphone gets a word here too, but only while it is doing
        # something: a closed microphone is the normal case and needs no notice.
        voice = telemetry.voice
        if voice.state.value not in ("OFF", ""):
            line = f"{line} | MIC {voice.state.value}"
        return line

    def render_shutdown(self, dt: float) -> bool:
        """Render one frame of the shutdown sequence.

        Returns True once the sequence has finished. The application has already
        released control, stopped the tracker and closed the camera before this
        is called, so nothing here can delay a safety action.
        """
        finished = self.shutdown_screen.update(dt)
        self.shutdown_screen.render(
            self.surface, pygame.Rect(0, 0, self.width, self.height), self.fonts
        )
        pygame.display.flip()
        return finished

    def close(self) -> None:
        """Cleanly close the window and quit pygame display."""
        self.ai_panel.clear_input()
        logger.debug("Closing application window...")
        try:
            pygame.display.quit()
        except Exception as exc:
            logger.warning("Error quitting display: %s", exc)
