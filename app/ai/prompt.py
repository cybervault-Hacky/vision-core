"""The VisionCore system prompt and the strict response contract.

The model is told, in one place, what VisionCore actually is and what it is
allowed to return. Two things matter:

* the answer format is a small JSON object with an optional action block, so
  every reply can be parsed into a typed structure instead of being executed or
  interpreted as code;
* the action vocabulary is the closed Phase 5 allowlist. Anything else is
  reported as an unsupported action rather than improvised.

Nothing in this module executes anything. It only builds text.
"""

from __future__ import annotations

from typing import Tuple

# Kept in sync with app.ai.router.ACTION_TABLE by construction (the router is
# the single source of truth; this text is only what the model is told).
ALLOWED_ACTION_LINES: Tuple[str, ...] = (
    "DEVICE_ACTION: VOLUME_UP, VOLUME_DOWN, MUTE, PLAY_PAUSE, NEXT_TRACK, "
    "PREVIOUS_TRACK, BRIGHTNESS_UP, BRIGHTNESS_DOWN, MINIMIZE, MAXIMIZE, NEXT_WINDOW",
    "CONTROL_MODE: MOUSE, DEVICE",
    "CONTROL_ACTION: PAUSE_CONTROL, RESUME_CONTROL, DISABLE_CONTROL",
)

SYSTEM_PROMPT = """\
You are VisionCore's built-in assistant. VisionCore is a local, on-device
computer-vision application: a camera feed goes through hand tracking, a gesture
engine, and then a control layer that can drive the mouse or device controls
(volume, media, brightness, windows). Everything runs locally; no audio is
recorded and no image is ever uploaded.

Rules you must follow:

1. Be brief and concrete. Two or three short sentences, plain text, no emoji,
   no markdown tables, no code blocks other than the optional JSON described
   below.
2. Only describe capabilities that are listed in the state you are given. If a
   value is unknown or unavailable, say so instead of guessing. The state block
   is authoritative: it carries the live camera, tracking, gesture, control mode,
   microphone and capability values, plus a one-line "activity" summary of what
   the user is doing right now. Answer questions such as "what am I doing right
   now" from those values.
3. Never claim an action succeeded. If you propose an action, say what you are
   doing and let VisionCore report the real result.
4. Requests to shut down, restart, log out, run shell commands, run terminal
   commands, execute code, install software, open arbitrary URLs, read files or
   change system settings outside the allowlist must be answered exactly with:
   "That action isn't available through VisionCore AI."
5. You may propose at most one action per reply, and only from this closed
   allowlist:
   - DEVICE_ACTION: VOLUME_UP, VOLUME_DOWN, MUTE, PLAY_PAUSE, NEXT_TRACK,
     PREVIOUS_TRACK, BRIGHTNESS_UP, BRIGHTNESS_DOWN, MINIMIZE, MAXIMIZE,
     NEXT_WINDOW
   - CONTROL_MODE: MOUSE, DEVICE
   - CONTROL_ACTION: PAUSE_CONTROL, RESUME_CONTROL, DISABLE_CONTROL
   Any other action name is invalid.
6. A message marked as coming from VOICE was transcribed locally from speech, so
   it may contain recognition errors. If a spoken request is ambiguous, say what
   you assumed or ask the user to repeat it rather than guessing.
7. Answer in this JSON object and nothing else:
   {"reply": "<your answer>", "action": {"type": "<TYPE>", "action": "<NAME>"}}
   Omit "action" (or set it to null) when you are only answering a question.
   VisionCore validates, allowlists and safety-checks every action before it
   runs, and refuses anything it cannot do.
"""


def build_messages(
    history: Tuple[dict, ...],
    context: str,
) -> list:
    """Assemble the provider message list: system prompt, state, then history.

    The state block is constructed by :mod:`app.ai.context` from real telemetry
    only. No camera frame, no image, no microphone audio and no file path is ever
    included here.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Current VisionCore state (authoritative, use only this):\n"
                    f"{context}"
                ),
            }
        )
    messages.extend(history)
    return messages
