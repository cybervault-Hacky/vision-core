"""Unit tests for UI components and interactive button elements."""

from __future__ import annotations

import pygame
import pytest

from ui.hud import HUDManager, get_status_color, COLOR_ONLINE, COLOR_STANDBY, COLOR_DISABLED, COLOR_ERROR
from ui.window import UIButton
from app.state import SubsystemState, Telemetry


def test_status_color_mapping():
    assert get_status_color(SubsystemState.ONLINE) == COLOR_ONLINE
    assert get_status_color(SubsystemState.STANDBY) == COLOR_STANDBY
    assert get_status_color(SubsystemState.DISABLED) == COLOR_DISABLED
    assert get_status_color(SubsystemState.ERROR) == COLOR_ERROR


def test_ui_button_hover_and_click():
    clicked = []

    def on_click():
        clicked.append(True)

    btn = UIButton(
        rect=pygame.Rect(10, 10, 100, 40),
        text="CLICK ME",
        is_primary=True,
        callback=on_click,
    )

    # Mouse motion outside
    motion_out = pygame.event.Event(pygame.MOUSEMOTION, {"pos": (200, 200)})
    btn.handle_event(motion_out)
    assert btn.is_hovered is False

    # Mouse motion inside
    motion_in = pygame.event.Event(pygame.MOUSEMOTION, {"pos": (50, 30)})
    btn.handle_event(motion_in)
    assert btn.is_hovered is True

    # Mouse click inside
    click_in = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (50, 30), "button": 1})
    was_clicked = btn.handle_event(click_in)
    assert was_clicked is True
    assert len(clicked) == 1

    # Mouse click outside
    click_out = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (500, 300), "button": 1})
    was_clicked_out = btn.handle_event(click_out)
    assert was_clicked_out is False
    assert len(clicked) == 1
