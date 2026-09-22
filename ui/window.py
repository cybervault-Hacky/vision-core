"""Main application desktop window, event loop, and layout manager."""

from __future__ import annotations

import logging
import os
import shutil
import warnings
from typing import Callable, Dict, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.config import AppConfig
from app.controls import ControlSnapshot
from app.gestures import GestureSnapshot
from app.hand_tracking import TrackingSnapshot
from app.state import AppState, Telemetry
from ui.animations import PulseAnimation
from ui.boot_screen import BootScreen
from ui.camera_view import CameraView
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
    ):
        self.config = config
        self.telemetry = telemetry
        self.on_retry_camera = on_retry_camera
        self.on_exit = on_exit
        self.on_control_toggle = on_control_toggle
        self.on_control_disable = on_control_disable

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
        self.hud_manager = HUDManager()
        self.camera_view = CameraView()
        self.pulse = PulseAnimation(min_val=0.4, max_val=1.0, frequency_hz=1.0)

        # Interactive error recovery buttons
        self.btn_retry: Optional[UIButton] = None
        self.btn_exit: Optional[UIButton] = None
        self._update_error_buttons()

        # Sidebar control rectangles, refreshed every rendered frame so clicks
        # always land on the button the user can actually see.
        self.control_toggle_rect: Optional[pygame.Rect] = None
        self.control_disable_rect: Optional[pygame.Rect] = None

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

            # Mouse control buttons live in the sidebar telemetry panels.
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self._handle_control_click(event.pos):
                    continue

            # Handle interactive buttons if in error state
            if self.telemetry.app_state == AppState.CAMERA_ERROR:
                if self.btn_retry and self.btn_retry.handle_event(event):
                    pass
                if self.btn_exit and self.btn_exit.handle_event(event):
                    return False

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

        sidebar_width = 280
        sidebar_x = self.width - sidebar_width - 16
        viewport_width = max(200, sidebar_x - 32)
        viewport_rect = pygame.Rect(16, content_top, viewport_width, content_height)

        # Render Video Viewport with HUD overlay
        self.camera_view.render(
            self.surface,
            viewport_rect,
            frame,
            self.telemetry,
            self.fonts,
            tracking,
            gesture,
        )

        # 4. Telemetry Panels in Sidebar (five stacked modules)
        panel_gap = 10
        rects = self._stack_panels(
            sidebar_x, sidebar_width, content_top, content_height, panel_gap
        )
        matrix_rect = rects["matrix"]
        tracking_rect = rects["tracking"]
        gesture_rect = rects["gesture"]
        control_rect = rects["control"]
        camera_rect = rects["camera"]

        self.hud_manager.draw_system_matrix_panel(self.surface, matrix_rect, self.telemetry, self.fonts)
        self.hud_manager.draw_tracking_panel(self.surface, tracking_rect, self.telemetry, self.fonts)
        self.hud_manager.draw_gesture_panel(self.surface, gesture_rect, self.telemetry, self.fonts)
        self.control_toggle_rect, self.control_disable_rect = self.hud_manager.draw_control_panel(
            self.surface, control_rect, self.telemetry, self.fonts
        )
        self.hud_manager.draw_camera_status_panel(self.surface, camera_rect, self.telemetry, self.fonts)

    def _stack_panels(
        self,
        x: int,
        width: int,
        top: int,
        height: int,
        gap: int,
    ) -> Dict[str, pygame.Rect]:
        """Lay out the sidebar modules so nothing is ever clipped.

        Each module declares the height it needs to be fully readable. The surplus
        is shared out in proportion to how much information each module carries;
        when the window is too short for every module to have its ideal height,
        all of them shrink together and the row renderers drop their supplementary
        rows rather than overlapping.
        """
        specs = (
            ("matrix", 114, 0.26),
            ("tracking", 126, 0.30),
            ("gesture", 100, 0.20),
            ("control", 146, 0.12),
            ("camera", 90, 0.12),
        )
        available = max(200, height - gap * (len(specs) - 1))
        total_min = sum(minimum for _, minimum, _ in specs)

        if available >= total_min:
            surplus = available - total_min
            heights = {
                name: minimum + int(surplus * weight)
                for name, minimum, weight in specs
            }
        else:
            scale = available / total_min
            heights = {name: max(62, int(minimum * scale)) for name, minimum, _ in specs}

        # Absorb rounding so the columns add up exactly: the last module would
        # otherwise lose a few pixels to the others on every window size.
        remainder = available - sum(heights.values())
        heights[specs[-1][0]] += remainder

        rects: Dict[str, pygame.Rect] = {}
        y = top
        for name, _minimum, _weight in specs:
            panel_height = max(62, heights[name])
            rects[name] = pygame.Rect(x, y, width, panel_height)
            y += panel_height + gap
        return rects

    def render_frame(
        self,
        frame: Optional[any],
        dt: float,
        tracking: TrackingSnapshot,
        gesture: GestureSnapshot,
        control: ControlSnapshot,
    ) -> None:
        """Dispatch rendering based on current application state."""
        self.pulse.update(dt)
        self.hud_manager.update(dt)
        self.camera_view.update(dt, tracking, gesture)

        state = self.telemetry.app_state

        if state == AppState.BOOTING:
            self.boot_screen.update(dt)
            self.boot_screen.render(self.surface, pygame.Rect(0, 0, self.width, self.height), self.fonts)
        elif state == AppState.CAMERA_ACTIVE:
            self.render_active_hud(frame, tracking, gesture, control)
        elif state == AppState.CAMERA_ERROR:
            self.render_error_screen()

        pygame.display.flip()

    def close(self) -> None:
        """Cleanly close the window and quit pygame display."""
        logger.debug("Closing application window...")
        try:
            pygame.display.quit()
        except Exception as exc:
            logger.warning("Error quitting display: %s", exc)
