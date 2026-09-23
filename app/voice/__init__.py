"""Voice input for VisionCore (Phase 8) - optional, local and deliberate.

Voice is one more way to *type into the assistant*, not a second assistant and
not a second control path::

    Voice  ->  transcript  ->  AI assistant  ->  structured intent
                                                   |
    Text   ->  message ---------------------------> same parser
                                                   |
                                                   v
                                    allowlist -> safety gate -> controllers

Four properties are non-negotiable and are enforced by the code, not by
documentation:

* **Off by default.** The microphone is closed until the user presses ``V`` or
  clicks the microphone control. Nothing opens it because a hand appeared, the
  assistant panel opened, the camera started or the application launched, and
  there is no wake word and no always-listening mode.
* **Local only.** The engines this package can use (Vosk, PocketSphinx) run
  offline on the machine. No recogniser in this package uploads audio, and the
  cloud recognisers the optional ``speech_recognition`` package also offers are
  never called.
* **Cancellable and bounded.** Listening ends by itself when the window expires,
  a second activation cancels it, and a cancelled transcript is discarded - it
  can never execute after the fact.
* **Honest.** With no engine or no microphone the state is ``VOICE UNAVAILABLE``
  with the real reason. A transcript is only ever reported when an engine
  actually produced one; nothing here invents speech.

Nothing in this package touches a controller, the operating system or the
network, and nothing is written to disk: a capture exists in memory for the
duration of one recognition call.
"""

# The package deliberately imports nothing at module scope. The AI context only
# needs :mod:`app.voice.types`, and re-exporting the worker and engine layer from
# here would drag them in (and their imports of :mod:`app.ai`) every time the
# state module is imported. Consumers import the submodule they need:
#
#     from app.voice.controller import VoiceController
#     from app.voice.recognizer import create_recognizer
#     from app.voice.settings import load_speech_settings
#     from app.voice.commands import match_command
#     from app.voice.types import SpeechResult, VoiceSnapshot, VoiceState
