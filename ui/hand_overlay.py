"""Futuristic hand tracking overlay for the VisionCore camera viewport.

Renders the tracked hand as an integrated part of the interface rather than a
debug drawing: soft bloom under thin bright bones, joint rings, animated tracking
brackets, a lock-on pulse and unobtrusive telemetry readouts.

All drawing is done on cached, size keyed alpha layers so the per frame cost stays
flat and the render loop remains allocation free.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from app.hand_tracking import (
    HAND_CONNECTIONS,
    PALM_CONNECTIONS,
    WRIST,
    Hand,
    Landmark,
    TrackingSnapshot,
    TrackingState,
)
from ui.animations import PulseAnimation, ScanlineAnimation
from ui.hud import (
    COLOR_CYAN_PRIMARY,
    COLOR_ICE_BLUE,
    COLOR_ONLINE,
    COLOR_PANEL_BORDER,
    COLOR_STANDBY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WHITE,
)

# Secondary accent for additional hands (keeps the palette restrained)
COLOR_HAND_SECONDARY = (150, 200, 255)

# Animation timings (seconds)
LOCK_DURATION = 0.45
GHOST_FADE_DURATION = 0.65
STATE_FLASH_DURATION = 0.30

# Fingertip landmarks get a slightly stronger marker than the phalanges
FINGERTIPS = (4, 8, 12, 16, 20)

# Padding around a hand used for its cached render layer
MARGIN = 26

Point = Tuple[float, float]


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class HandOverlay:
    """Draws tracked hands, lock-on animation and tracking readouts."""

    def __init__(self) -> None:
        self._pulse = PulseAnimation(min_val=0.45, max_val=1.0, frequency_hz=1.5)
        self._sweep = ScanlineAnimation(speed=0.32)

        self._lock_progress = 0.0
        self._state_flash = 0.0
        self._ghost: Tuple[Landmark, ...] = ()
        self._ghost_alpha = 0.0
        self._previous_state = TrackingState.NO_HAND

        self._layers: Dict[Tuple[str, int, int], pygame.Surface] = {}

    # -- animation --------------------------------------------------------- #

    def update(self, dt: float, snapshot: TrackingSnapshot) -> None:
        """Advance overlay animations from the current tracking snapshot."""
        self._pulse.update(dt)
        state = snapshot.state

        if state.is_engaged:
            self._lock_progress = min(1.0, self._lock_progress + dt / LOCK_DURATION)
        else:
            self._lock_progress = 0.0

        if state is TrackingState.NO_HAND:
            self._sweep.update(dt)

        if state != self._previous_state:
            self._state_flash = 1.0
            self._previous_state = state

        self._state_flash = max(0.0, self._state_flash - dt / STATE_FLASH_DURATION)

        # The pose trace survives the tracker's hold window and only fades once
        # the hand is officially lost, so transitions never snap.
        if state.is_engaged or snapshot.detected:
            self._ghost_alpha = 1.0
        elif self._ghost:
            self._ghost_alpha = max(0.0, self._ghost_alpha - dt / GHOST_FADE_DURATION)
            if self._ghost_alpha == 0.0:
                self._ghost = ()

    # -- rendering --------------------------------------------------------- #

    def render(
        self,
        surface: pygame.Surface,
        fitted_rect: pygame.Rect,
        snapshot: TrackingSnapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render the complete hand tracking layer inside the video viewport."""
        hands = snapshot.hands

        if snapshot.detected and hands:
            self._ghost = hands[0].landmarks
            for index, hand in enumerate(hands):
                self._draw_hand(surface, fitted_rect, hand, index, snapshot, fonts)
            self._draw_status_block(surface, fitted_rect, snapshot, fonts)
        else:
            if self._ghost and self._ghost_alpha > 0.0:
                holding = snapshot.state.is_engaged
                alpha = 0.55 + 0.25 * self._pulse.value if holding else self._ghost_alpha
                self._draw_ghost(surface, fitted_rect, alpha)
            self._draw_search_effects(surface, fitted_rect, snapshot, fonts)

    # -- hand visualisation ------------------------------------------------ #

    def _draw_hand(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        hand: Hand,
        index: int,
        snapshot: TrackingSnapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Render one hand: bloom, bones, joints, brackets and lock animation."""
        points = self._to_pixels(hand.landmarks, fitted)
        if len(points) < 2:
            return

        accent = COLOR_CYAN_PRIMARY if index == 0 else COLOR_HAND_SECONDARY
        connections = [
            (a, b) for a, b in HAND_CONNECTIONS if a < len(points) and b < len(points)
        ]

        # Layout: hand geometry plus the label that hangs off its top edge.
        hand_rect = pygame.Rect(
            int(min(p[0] for p in points)),
            int(min(p[1] for p in points)),
            max(1, int(max(p[0] for p in points) - min(p[0] for p in points))),
            max(1, int(max(p[1] for p in points) - min(p[1] for p in points))),
        )
        tag_text = self._tag_label(hand, index, fonts, accent)
        tag_rect = self._place_tag(
            (tag_text.get_width() + 18, tag_text.get_height() + 8), hand_rect, fitted
        )

        canvas = hand_rect.inflate(MARGIN * 2, MARGIN * 2).union(tag_rect.inflate(6, 6))
        canvas = canvas.clip(surface.get_rect())
        if canvas.width < 8 or canvas.height < 8:
            return

        offset = canvas.topleft
        local = [(p[0] - offset[0], p[1] - offset[1]) for p in points]
        tag_local = pygame.Rect(
            tag_rect.x - offset[0], tag_rect.y - offset[1], tag_rect.width, tag_rect.height
        )

        bloom = self._layer("bloom", canvas.size)
        core = self._layer("core", canvas.size)
        soft = self._layer("soft", canvas.size)

        # Pass 1 — bloom: wide halo beneath the geometry.
        for a, b in connections:
            width = 9 if (a, b) in PALM_CONNECTIONS else 7
            pygame.draw.line(bloom, (*accent, 40), local[a], local[b], width)
        for point in local:
            pygame.draw.circle(bloom, (*accent, 38), point, 8)

        # Pass 2 — core: thin bright bones.
        for a, b in connections:
            is_palm = (a, b) in PALM_CONNECTIONS
            pygame.draw.line(
                core,
                (*accent, 200 if is_palm else 150),
                local[a],
                local[b],
                2 if is_palm else 1,
            )

        # Pass 3 — soft: delicate white highlights on joints and fingertips.
        for i, point in enumerate(local):
            pygame.draw.circle(soft, (*COLOR_ICE_BLUE, 150), point, 2)
            pygame.draw.circle(soft, (*accent, 60), point, 5, 1)
            if i in FINGERTIPS:
                pygame.draw.circle(soft, (*COLOR_ICE_BLUE, 190), point, 3)
                pygame.draw.circle(soft, (*accent, 130), point, 7, 1)
        if len(local) > WRIST:
            pygame.draw.circle(soft, (*COLOR_ICE_BLUE, 200), local[WRIST], 3)
            pygame.draw.circle(soft, (*accent, 110), local[WRIST], 9, 1)

        self._draw_lock_geometry(bloom, core, local, accent, snapshot)

        # Label leader line, drawn from the hand's top edge to the tag.
        anchor = (min(local, key=lambda p: p[1])[0], min(p[1] for p in local))
        leader = (tag_local.centerx, tag_local.bottom)
        if abs(leader[1] - anchor[1]) > 3:
            pygame.draw.line(soft, (*accent, 110), anchor, leader, 1)
            pygame.draw.circle(soft, (*accent, 170), anchor, 2)

        # Tag body.
        pygame.draw.rect(core, (8, 16, 26, 205), tag_local)
        pygame.draw.rect(core, (*COLOR_PANEL_BORDER, 225), tag_local, 1)
        pygame.draw.line(
            core, (*accent, 235), tag_local.topleft, (tag_local.left, tag_local.bottom), 2
        )
        core.blit(tag_text, (tag_local.x + 11, tag_local.y + 4))

        surface.blit(bloom, canvas.topleft)
        surface.blit(soft, canvas.topleft)
        surface.blit(core, canvas.topleft)

    def _draw_lock_geometry(
        self,
        bloom: pygame.Surface,
        core: pygame.Surface,
        local: Sequence[Point],
        accent: Tuple[int, int, int],
        snapshot: TrackingSnapshot,
    ) -> None:
        """Animated tracking brackets, tracking region and lock-on pulse."""
        pad = 16
        xs = [p[0] for p in local]
        ys = [p[1] for p in local]
        region = pygame.Rect(
            min(xs) - pad,
            min(ys) - pad,
            (max(xs) - min(xs)) + pad * 2,
            (max(ys) - min(ys)) + pad * 2,
        )

        progress = _ease_out_cubic(self._lock_progress)
        locked = snapshot.state is TrackingState.TRACKING
        searching = not snapshot.state.is_engaged

        # Brackets converge inward while locking on and settle on the hand.
        converge = int((1.0 - progress) * 26)
        bracket = region.inflate(converge * 2, converge * 2)
        arm = int(10 + 16 * progress)
        alpha = 60 if searching else int(120 + 120 * progress)

        # Quiet tracking region outline.
        pygame.draw.rect(core, (*accent, 70 if not locked else 90), region, 1)

        for corner, dx, dy in (
            (bracket.topleft, 1, 1),
            (bracket.topright, -1, 1),
            (bracket.bottomleft, 1, -1),
            (bracket.bottomright, -1, -1),
        ):
            x, y = corner
            pygame.draw.line(core, (*accent, alpha), (x, y), (x + arm * dx, y), 2)
            pygame.draw.line(core, (*accent, alpha), (x, y), (x, y + arm * dy), 2)

        # Lock-on pulse: a single expanding ring that fades as it travels.
        if progress < 1.0 and snapshot.state.is_engaged:
            radius = int(18 + progress * 70)
            fade = int(150 * (1.0 - progress))
            if fade > 0:
                pygame.draw.circle(
                    bloom,
                    (*accent, fade),
                    (region.centerx, region.centery),
                    radius,
                    2,
                )

        # Locked marker: a spark on the region's leading edge.
        if locked:
            spark = int(120 + 135 * self._pulse.value)
            pygame.draw.line(
                core,
                (*COLOR_ONLINE, spark),
                (region.left, region.top),
                (region.left + 6, region.top),
                2,
            )

    @staticmethod
    def _tag_label(
        hand: Hand,
        index: int,
        fonts: Dict[str, pygame.font.Font],
        accent: Tuple[int, int, int],
    ) -> pygame.Surface:
        """Build the small identity chip rendered above a tracked hand."""
        label = f"TRK-{index + 1:02d}"
        if hand.handedness:
            label += f"  {hand.handedness}"
        if hand.confidence is not None:
            label += f"  {hand.confidence * 100:.1f}%"
        return fonts["mono_small"].render(label, True, accent)

    @staticmethod
    def _place_tag(
        size: Tuple[int, int],
        hand_rect: pygame.Rect,
        fitted: pygame.Rect,
    ) -> pygame.Rect:
        """Position the label above the hand, kept inside the viewport."""
        width, height = size
        x = hand_rect.centerx - width // 2
        y = hand_rect.top - height - 14
        x = int(min(max(fitted.left + 8, x), fitted.right - width - 8))
        y = int(min(max(fitted.top + 8, y), fitted.bottom - height - 8))
        return pygame.Rect(x, y, width, height)

    def _draw_ghost(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        alpha: float,
    ) -> None:
        """Dim trace of the last known pose while the HUD searches."""
        points = self._to_pixels(self._ghost, fitted)
        if len(points) < 2:
            return

        bounds = self._bounds(points, margin=20, clip=surface.get_rect())
        if bounds is None:
            return

        offset = bounds.topleft
        local = [(px - offset[0], py - offset[1]) for px, py in points]
        layer = self._layer("ghost", bounds.size)

        line_alpha = int(120 * alpha)
        node_alpha = int(150 * alpha)

        for a, b in HAND_CONNECTIONS:
            pygame.draw.line(layer, (*COLOR_STANDBY, line_alpha), local[a], local[b], 1)
        for point in local:
            pygame.draw.circle(layer, (*COLOR_STANDBY, node_alpha), point, 2)

        # Released tracking region: open corner brackets rather than a hard box.
        released = bounds.inflate(-6, -6)
        bracket_alpha = int(95 * alpha)
        for x, y, dx, dy in (
            (released.left, released.top, 1, 1),
            (released.right, released.top, -1, 1),
            (released.left, released.bottom, 1, -1),
            (released.right, released.bottom, -1, -1),
        ):
            pygame.draw.line(layer, (*COLOR_STANDBY, bracket_alpha), (x, y), (x + 14 * dx, y), 1)
            pygame.draw.line(layer, (*COLOR_STANDBY, bracket_alpha), (x, y), (x, y + 14 * dy), 1)

        surface.blit(layer, bounds.topleft)

    # -- search state ------------------------------------------------------ #

    def _draw_search_effects(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        snapshot: TrackingSnapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Ambient scanning sweep and status readout while no hand is tracked."""
        scan_y = int(fitted.y + self._sweep.position * fitted.height)
        beam_height = 22
        beam_y = max(fitted.y, scan_y - beam_height)
        if fitted.height > beam_height:

            beam = self._layer("beam", (fitted.width, beam_height))
            for row in range(beam_height):
                alpha = int(34 * (row / beam_height) ** 2)
                pygame.draw.line(beam, (*COLOR_CYAN_PRIMARY, alpha), (0, row), (fitted.width, row))
            surface.blit(beam, (fitted.x, beam_y))
            pygame.draw.line(
                surface,
                (0, 120, 150),
                (fitted.x, scan_y),
                (fitted.right, scan_y),
                1,
            )

        self._draw_status_block(surface, fitted, snapshot, fonts)

    # -- status block ------------------------------------------------------ #

    def _draw_status_block(
        self,
        surface: pygame.Surface,
        fitted: pygame.Rect,
        snapshot: TrackingSnapshot,
        fonts: Dict[str, pygame.font.Font],
    ) -> None:
        """Bottom-left readout describing the real tracking state.

        Rows are dropped from the bottom up until the block fits inside the video
        area, so a short window shows the tracking state and the hand count
        instead of a panel that spills out of the viewport.
        """
        state = snapshot.state
        hand = snapshot.primary

        holding = state.is_engaged and not snapshot.detected
        rows: List[Tuple[str, str, Tuple[int, int, int]]] = [
            (
                "TRACKING",
                "HOLD" if holding else state.status_label,
                COLOR_STANDBY if holding else self._state_color(state),
            )
        ]

        if hand is not None:
            if hand.confidence is not None:
                rows.append(("CONFIDENCE", f"{hand.confidence * 100:.1f}%", COLOR_ICE_BLUE))
            if hand.handedness:
                rows.append(("HAND", hand.handedness, COLOR_TEXT_WHITE))
            if len(snapshot.hands) > 1:
                rows.append(("HANDS", str(len(snapshot.hands)), COLOR_ICE_BLUE))

        banner = "TRACKING HOLD" if holding else self._banner_text(state)
        banner_color = COLOR_STANDBY if holding else self._state_color(state)

        padding = 14
        row_height = 17
        banner_height = 24
        # A narrow video area cannot host this block, the central focus readout
        # and the gesture readout at once, so the block is skipped rather than
        # crowded against them: the same readings stay available in the sidebar
        # (HAND TRACKING module) and in the focus quality chip.
        if fitted.width < 560:
            return
        width = min(244, fitted.width - 28)
        available = max(88, fitted.height - 28)

        def panel_height(count: int) -> int:
            return padding * 2 + banner_height + row_height * count + 4

        while len(rows) > 1 and panel_height(len(rows)) > available:
            rows.pop()
        height = panel_height(len(rows))

        panel = self._layer("status", (width, height))
        panel.fill((*(7, 14, 23), 205))
        pygame.draw.rect(panel, (*COLOR_PANEL_BORDER, 230), panel.get_rect(), 1)
        pygame.draw.line(panel, (*banner_color, 220), (0, 0), (0, height), 2)
        pygame.draw.line(
            panel,
            (*COLOR_PANEL_BORDER, 200),
            (padding, padding + banner_height + 1),
            (width - padding, padding + banner_height + 1),
            1,
        )

        # Banner: animated indicator plus state headline.
        dot_x = padding + 3
        dot_y = padding + banner_height // 2
        dot_alpha = int(150 + 105 * self._pulse.value)
        pygame.draw.circle(panel, (*banner_color, dot_alpha), (dot_x, dot_y), 3)
        if state.is_engaged:
            pygame.draw.circle(
                panel,
                (*banner_color, int(70 * (1.0 - self._pulse.value) + 30)),
                (dot_x, dot_y),
                int(4 + 4 * self._pulse.value),
                1,
            )

        # State transition flash: a brief highlight sweep through the panel.
        flash = self._state_flash
        if flash > 0.0:
            sweep_alpha = int(150 * flash)
            sweep_width = int(14 + 60 * (1.0 - flash))
            sweep = self._layer("flash", (sweep_width, height))
            for column in range(sweep_width):
                column_alpha = int(sweep_alpha * (1.0 - column / sweep_width))
                pygame.draw.line(
                    sweep, (*banner_color, column_alpha), (column, 0), (column, height)
                )
            panel.blit(sweep, (int((width - sweep_width) * (1.0 - flash)), 0))

        banner_surface = fonts["caption"].render(banner, True, banner_color)
        panel.blit(banner_surface, (padding + 14, padding + 4))

        offset = padding + banner_height + 2
        for label, value, value_color in rows:
            label_surface = fonts["mono_small"].render(label, True, COLOR_TEXT_MUTED)
            panel.blit(label_surface, (padding, offset + 2))
            value_surface = fonts["mono_small"].render(value, True, value_color)
            panel.blit(
                value_surface,
                (width - padding - value_surface.get_width(), offset + 2),
            )
            offset += row_height

        surface.blit(panel, (fitted.left + 14, fitted.bottom - height - 14))

    @staticmethod
    def _banner_text(state: TrackingState) -> str:
        if state is TrackingState.TRACKING:
            return "HAND DETECTED"
        if state is TrackingState.DETECTING:
            return "HAND DETECTED  //  LOCKING"
        if state is TrackingState.HAND_LOST:
            return "HAND LOST"
        return "SCANNING FOR HAND"

    @staticmethod
    def _state_color(state: TrackingState) -> Tuple[int, int, int]:
        if state is TrackingState.TRACKING:
            return COLOR_CYAN_PRIMARY
        if state is TrackingState.DETECTING:
            return COLOR_ICE_BLUE
        if state is TrackingState.HAND_LOST:
            return COLOR_STANDBY
        return COLOR_TEXT_MUTED

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _to_pixels(landmarks: Sequence[Landmark], fitted: pygame.Rect) -> List[Point]:
        """Map normalised (mirrored) landmarks onto the displayed video rect."""
        width = fitted.width
        height = fitted.height
        return [
            (fitted.x + lm.x * width, fitted.y + lm.y * height)
            for lm in landmarks
        ]

    @staticmethod
    def _bounds(
        points: Sequence[Point],
        margin: int,
        clip: pygame.Rect,
    ) -> Optional[pygame.Rect]:
        """Bounding rect around a point cloud, padded and clipped to the surface."""
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        rect = pygame.Rect(
            int(min(xs)) - margin,
            int(min(ys)) - margin,
            int(max(xs) - min(xs)) + margin * 2,
            int(max(ys) - min(ys)) + margin * 2,
        )
        clipped = rect.clip(clip)
        return clipped if clipped.width > 4 and clipped.height > 4 else None

    def _layer(self, kind: str, size: Tuple[int, int]) -> pygame.Surface:
        """Return a cleared, cached alpha layer of the requested kind and size."""
        width = max(1, int(size[0]))
        height = max(1, int(size[1]))
        key = (kind, width, height)
        layer = self._layers.get(key)
        if layer is None:
            if len(self._layers) > 16:
                self._layers.clear()
            layer = pygame.Surface((width, height), pygame.SRCALPHA)
            self._layers[key] = layer
        else:
            layer.fill((0, 0, 0, 0))
        return layer
