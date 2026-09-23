"""Reusable action feedback for VisionCore.

Every notification the interface shows is created here from a real action
result: a mouse click the backend accepted, a device action that was actually
performed, a control mode change or a safety transition. Nothing is fabricated
and nothing is shown as a success when the backend refused the action -
refusals are reported as refusals.

Two views are produced from the same entries:

* a single transient notification (the toast) that animates in, holds, fades out
  and can be replaced at any time;
* a short recent-action timeline kept in memory only, newest first, capped at
  :data:`TIMELINE_LIMIT` rows. It is never written to disk and is cleared when
  the application exits, so no personal activity history survives a session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from app.interaction.states import ActionTier

# Notification timing (seconds).
FEEDBACK_DURATION = 1.70
FEEDBACK_ANIM_IN = 0.16
FEEDBACK_FADE_OUT = 0.36
# Identical actions repeated inside this window merge into one row with a
# counter instead of filling the timeline (scrolling would otherwise flood it).
FEEDBACK_COALESCE_SEC = 0.60

TIMELINE_LIMIT = 8


class FeedbackSource(str, Enum):
    """Which layer produced the event."""

    MOUSE = "MOUSE"
    DEVICE = "DEVICE"
    MODE = "MODE"
    SAFETY = "SAFETY"
    SYSTEM = "SYSTEM"

    @property
    def label(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ActionFeedback:
    """One notification entry (transient toast and/or timeline row)."""

    token: int
    label: str
    source: FeedbackSource
    tier: ActionTier
    success: bool = True
    detail: str = ""
    created_at: float = 0.0
    clock_label: str = ""
    repeat: int = 1
    sticky: bool = False

    def age(self, now: float) -> float:
        """Seconds since the entry was created (never negative)."""
        return max(0.0, now - self.created_at)

    def alpha(self, now: float, duration: float = FEEDBACK_DURATION) -> float:
        """Opacity for this entry: animated in, held, then faded out."""
        age = self.age(now)
        if age >= duration:
            return 0.0
        if age < FEEDBACK_ANIM_IN:
            return age / FEEDBACK_ANIM_IN
        remaining = duration - age
        if remaining < FEEDBACK_FADE_OUT:
            return remaining / FEEDBACK_FADE_OUT
        return 1.0

    def progress(self, now: float, duration: float = FEEDBACK_DURATION) -> float:
        """Life progress in ``[0, 1]``, used by the draining underline."""
        return min(1.0, self.age(now) / duration)

    @property
    def display_label(self) -> str:
        """Label plus a repeat counter when an identical action merged in."""
        return f"{self.label}  x{self.repeat}" if self.repeat > 1 else self.label


class FeedbackCenter:
    """Owns the notification and the in-memory recent-action timeline."""

    def __init__(self, limit: int = TIMELINE_LIMIT) -> None:
        self.limit = max(1, int(limit))
        self._entries: List[ActionFeedback] = []
        self._sticky: Optional[ActionFeedback] = None
        self._token = 0

    # -- recording --------------------------------------------------------- #

    def record(
        self,
        label: str,
        source: FeedbackSource,
        tier: ActionTier,
        success: bool = True,
        detail: str = "",
        now: float = 0.0,
        sticky: bool = False,
    ) -> ActionFeedback:
        """Add one real event to the notification and the timeline.

        An identical label from the same source inside
        :data:`FEEDBACK_COALESCE_SEC` updates the newest row (bumping its
        counter and timestamp) instead of adding another one, so a continuous
        action cannot flood the interface.
        """
        if not label:
            return self._empty()

        entry = self._build(label, source, tier, success, detail, now, sticky)

        if self._entries:
            newest = self._entries[0]
            if (
                newest.label == label
                and newest.source is source
                and newest.success == success
                and max(0.0, now - newest.created_at) <= FEEDBACK_COALESCE_SEC
            ):
                merged = ActionFeedback(
                    token=newest.token,
                    label=label,
                    source=source,
                    tier=tier,
                    success=success,
                    detail=detail or newest.detail,
                    created_at=now,
                    clock_label=entry.clock_label,
                    repeat=newest.repeat + 1,
                    sticky=sticky,
                )
                self._entries[0] = merged
                if self._sticky is not None and self._sticky.token == newest.token:
                    self._sticky = merged if sticky else None
                return merged

        self._entries.insert(0, entry)
        if len(self._entries) > self.limit:
            del self._entries[self.limit:]
        return entry

    def set_sticky(self, entry: Optional[ActionFeedback]) -> None:
        """Pin an entry so it stays visible until it is cleared.

        Used for a safety state: while an emergency stop is active the
        notification must not fade away on its own.
        """
        self._sticky = entry

    def clear_sticky(self) -> None:
        self._sticky = None

    def clear(self) -> None:
        """Drop every entry (application exit - nothing is persisted)."""
        self._entries.clear()
        self._sticky = None

    # -- views ------------------------------------------------------------- #

    def toast(self, now: float) -> Optional[ActionFeedback]:
        """The entry currently shown as a notification, if any.

        A sticky entry always wins; otherwise the newest entry is shown for
        :data:`FEEDBACK_DURATION` seconds.
        """
        if self._sticky is not None:
            return self._sticky
        if not self._entries:
            return None
        newest = self._entries[0]
        return newest if newest.age(now) < FEEDBACK_DURATION else None

    @property
    def timeline(self) -> Tuple[ActionFeedback, ...]:
        """Recent events, newest first (immutable view)."""
        return tuple(self._entries)

    @property
    def sticky(self) -> Optional[ActionFeedback]:
        return self._sticky

    # -- helpers ----------------------------------------------------------- #

    def _build(
        self,
        label: str,
        source: FeedbackSource,
        tier: ActionTier,
        success: bool,
        detail: str,
        now: float,
        sticky: bool,
    ) -> ActionFeedback:
        self._token += 1
        return ActionFeedback(
            token=self._token,
            label=label,
            source=source,
            tier=tier,
            success=success,
            detail=detail,
            created_at=now,
            clock_label=time.strftime("%H:%M:%S", time.localtime()),
            sticky=sticky,
        )

    def _empty(self) -> ActionFeedback:
        return ActionFeedback(
            token=self._token,
            label="",
            source=FeedbackSource.SYSTEM,
            tier=ActionTier.VISUAL_FEEDBACK,
        )
