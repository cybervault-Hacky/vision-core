"""Deterministic command phrases, shared by typed and spoken input.

When no AI provider is configured, VisionCore still understands a small, closed
set of unambiguous commands - because they do not need a language model. The
same vocabulary serves a typed message and a voice transcript, so the input
method never changes what VisionCore can do::

    "turn the volume up"      -> DEVICE_ACTION: VOLUME_UP
    "put me in device mode"   -> CONTROL_MODE: DEVICE
    "pause the mouse"         -> CONTROL_ACTION: PAUSE_CONTROL

Two rules keep this honest and safe:

* this module only *recognises phrases*; it never performs anything. It returns
  an :class:`~app.ai.types.AIActionPlan`, which travels the ordinary route -
  allowlist, mode check, control-enabled check, emergency check, capability
  check, existing controller - exactly like a plan that came from a model;
* the vocabulary is a closed list of literal phrases. There is no fuzzy
  matching, no inference and no improvisation: a phrase that is not on the list
  is simply not a command, and anything that reads as a question never matches.

The action names are not duplicated here either: every phrase resolves through
:func:`app.ai.router.resolve`, so the allowlist in ``app/ai/router.py`` stays the
single source of truth for what VisionCore can do.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from app.ai.router import resolve
from app.ai.types import AIActionKind, AIActionPlan

# Politeness wrappers stripped before matching, so "could you turn the volume up"
# and "turn the volume up" are the same command. Stripped at most twice.
_PREFIXES: Tuple[str, ...] = (
    "please",
    "kindly",
    "visioncore",
    "hey visioncore",
    "ok visioncore",
    "could you",
    "can you",
    "would you",
    "will you",
    "can you please",
    "i want you to",
    "i want to",
    "i would like you to",
    "i'd like you to",
    "i would like to",
    "i'd like to",
    "go ahead and",
    "let's",
    "let us",
)

# A phrase containing one of these reads as a question, never as a command.
_QUESTION_WORDS = frozenset(
    {
        "how", "what", "why", "when", "where", "who", "which", "whose",
        "does", "do", "did", "is", "are", "was", "were", "should", "explain",
        "tell", "say", "?" ,
    }
)

_PUNCTUATION = re.compile(r"[^a-z0-9\s']")

# (kind, action) -> spoken phrases. Literal matches only.
_COMMANDS: Dict[Tuple[AIActionKind, str], Tuple[str, ...]] = {
    (AIActionKind.DEVICE_ACTION, "VOLUME_UP"): (
        "volume up", "turn the volume up", "turn up the volume", "turn it up",
        "increase the volume", "increase volume", "raise the volume", "louder",
        "make it louder", "sound up", "volume higher",
    ),
    (AIActionKind.DEVICE_ACTION, "VOLUME_DOWN"): (
        "volume down", "turn the volume down", "turn down the volume",
        "turn it down", "decrease the volume", "decrease volume",
        "lower the volume", "quieter", "make it quieter", "sound down",
        "volume lower",
    ),
    (AIActionKind.DEVICE_ACTION, "MUTE"): (
        "mute", "mute the sound", "mute the volume", "toggle mute", "unmute",
        "silence",
    ),
    (AIActionKind.DEVICE_ACTION, "PLAY_PAUSE"): (
        "play", "pause", "play the music", "pause the music", "play music",
        "pause music", "resume the music", "resume playback", "play the song",
        "pause the song", "stop the music",
    ),
    (AIActionKind.DEVICE_ACTION, "NEXT_TRACK"): (
        "next track", "next song", "skip", "skip track", "skip song",
        "skip this song", "next",
    ),
    (AIActionKind.DEVICE_ACTION, "PREVIOUS_TRACK"): (
        "previous track", "previous song", "last track", "skip back",
        "previous", "back",
    ),
    (AIActionKind.DEVICE_ACTION, "BRIGHTNESS_UP"): (
        "brightness up", "increase the brightness", "increase brightness",
        "raise the brightness", "brighter", "make it brighter",
        "screen brighter", "display brighter",
    ),
    (AIActionKind.DEVICE_ACTION, "BRIGHTNESS_DOWN"): (
        "brightness down", "decrease the brightness", "decrease brightness",
        "lower the brightness", "dimmer", "make it dimmer", "screen dimmer",
        "display dimmer",
    ),
    (AIActionKind.DEVICE_ACTION, "MINIMIZE"): (
        "minimize", "minimise", "minimize the window", "minimise the window",
        "minimize window", "minimise window", "hide the window",
        "minimize the current window", "minimise the current window",
        "minimize this window", "minimise this window",
    ),
    (AIActionKind.DEVICE_ACTION, "MAXIMIZE"): (
        "maximize", "maximise", "maximize the window", "maximise the window",
        "maximize window", "maximise window", "maximize the current window",
        "maximise the current window", "maximize this window", "maximise this window",
    ),
    (AIActionKind.DEVICE_ACTION, "NEXT_WINDOW"): (
        "next window", "switch window", "switch windows", "switch app",
        "switch application", "next app", "alt tab",
    ),
    (AIActionKind.CONTROL_MODE, "DEVICE"): (
        "device mode", "switch to device mode", "change to device mode",
        "use device mode", "go to device mode", "device control mode",
        "put me in device mode", "put it in device mode", "set device mode",
        "set the device mode",
    ),
    (AIActionKind.CONTROL_MODE, "MOUSE"): (
        "mouse mode", "switch to mouse mode", "change to mouse mode",
        "use mouse mode", "pointer mode", "mouse control mode",
        "put me in mouse mode", "put it in mouse mode", "set mouse mode",
        "put me back in mouse mode",
    ),
    (AIActionKind.CONTROL_ACTION, "PAUSE_CONTROL"): (
        "pause the mouse", "pause control", "pause mouse control",
        "pause the pointer", "freeze the pointer", "stop the pointer",
    ),
    (AIActionKind.CONTROL_ACTION, "RESUME_CONTROL"): (
        "resume control", "resume the mouse", "resume mouse control",
        "unpause control", "resume the pointer",
    ),
    (AIActionKind.CONTROL_ACTION, "DISABLE_CONTROL"): (
        "disable control", "disable the mouse", "turn off control",
        "stop control", "release control",
    ),
}

# Reverse index: phrase -> (kind, action).
_PHRASE_INDEX: Dict[str, Tuple[AIActionKind, str]] = {
    phrase: key for key, phrases in _COMMANDS.items() for phrase in phrases
}

# Examples shown by the capability answer, in the order a user is likely to try.
_EXAMPLES: Tuple[str, ...] = (
    "turn the volume up",
    "play the music",
    "put me in device mode",
    "pause the mouse",
)


def normalise(text: str) -> str:
    """Lower-case, punctuation-free, prefix-stripped form used for matching."""
    clean = _PUNCTUATION.sub(" ", (text or "").lower())
    clean = " ".join(clean.split())
    for _ in range(2):
        for prefix in _PREFIXES:
            if clean == prefix:
                return ""
            if clean.startswith(prefix + " "):
                clean = clean[len(prefix) + 1 :].strip()
                break
    return clean


def looks_like_question(text: str) -> bool:
    """True when a phrase asks something instead of instructing."""
    if "?" in (text or ""):
        return True
    words = normalise(text).split()
    return bool(words) and words[0] in _QUESTION_WORDS


def match_command(text: str) -> Optional[AIActionPlan]:
    """Return the plan for a literal command phrase, or None.

    The plan is built from the router's own allowlist entry, so a phrase can
    never name an action VisionCore does not already support.
    """
    clean = normalise(text)
    if not clean or looks_like_question(text):
        return None

    target = _PHRASE_INDEX.get(clean)
    if target is None:
        # "turn the volume up now" and similar trailing words still match, but
        # only when the remainder is a literal phrase plus a known filler.
        target = _match_with_filler(clean)
    if target is None:
        return None

    kind, action = target
    spec = resolve(kind, action)
    if spec is None:
        # The allowlist is the authority: an entry that is not there is not a
        # command, and this can only happen if the two ever drift apart.
        return None
    return AIActionPlan(
        kind=spec.kind,
        action=spec.action,
        label=spec.label,
        requires_confirmation=spec.requires_confirmation,
    )


_FILLERS = ("a bit", "a little", "for me", "one step", "now", "please", "again", "slightly")


def _tail_is_filler(tail: List[str]) -> bool:
    """True when every trailing word is a known filler ("now", "please", "a bit")."""
    remaining = " ".join(tail)
    while remaining:
        for filler in _FILLERS:
            if remaining == filler:
                return True
            if remaining.startswith(filler + " "):
                remaining = remaining[len(filler) + 1 :]
                break
        else:
            return False
    return True


def _match_with_filler(clean: str) -> Optional[Tuple[AIActionKind, str]]:
    """Accept a phrase followed only by filler words ("volume up a bit").

    Anything else trailing the phrase means it is not the command it resembles:
    "play music loudly" is a request about loudness, not a play command.
    """
    words = clean.split()
    for cut in range(len(words) - 1, 0, -1):
        if not _tail_is_filler(words[cut:]):
            continue
        target = _PHRASE_INDEX.get(" ".join(words[:cut]))
        if target is not None:
            return target
    return None


def command_phrases() -> List[str]:
    """Every literal phrase VisionCore understands without a provider."""
    return sorted(_PHRASE_INDEX)


def spoken_examples() -> Tuple[str, ...]:
    """Short example commands used by the local capability answer."""
    return _EXAMPLES
