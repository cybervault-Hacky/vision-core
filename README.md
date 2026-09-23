# VisionCore v1.0.0

VisionCore is a local-first desktop application for camera-based hand tracking, gesture recognition, and explicitly enabled touchless controls. It presents a dark, futuristic HUD while keeping capability and hardware status honest: unavailable camera, tracking, microphone, AI, mouse, and device backends are reported as unavailable rather than simulated as ready.

This is the final VisionCore v1.0 release. No later roadmap phase is required.

## Features

- Camera HUD with an actual startup check, recovery screen, and bounded shutdown sequence.
- On-device MediaPipe hand landmark tracking with measured tracking telemetry.
- Geometry-based gestures: `OPEN_PALM`, `FIST`, `POINT`, `PINCH`, `TWO_FINGER`, `SWIPE_LEFT`, and `SWIPE_RIGHT`.
- Opt-in mouse control: pointer movement, one-click-per-pinch, drag, two-finger scrolling, pause, and emergency stop.
- Separate `MOUSE` and `DEVICE` modes. Device actions are gated by mode, safety state, and platform capability.
- Optional AI assistant using an OpenAI-compatible provider. AI produces only structured, allowlisted intents and cannot execute shell commands.
- Optional local voice input. The microphone is off at launch and opens only for one explicitly activated listening window.
- Local memory-only conversation and action feedback. Camera frames and raw microphone audio are not persisted.

## Requirements

- Python 3.10 or newer. MediaPipe wheel availability can vary by interpreter and operating system.
- A desktop environment with a display for the HUD.
- A supported camera for live tracking. VisionCore can start with `--mock-camera` when physical camera hardware is unavailable.
- Linux, Windows, or macOS for the application window. Mouse and device backends depend on the host: Linux mouse control requires an X11/XTEST session, and Windows uses its native input APIs. Unsupported hosts are reported honestly.

Required runtime packages are declared in `requirements.txt`:

- `opencv-python-headless` — camera capture and frame conversion
- `numpy` — frame and landmark mathematics
- `pygame` — window, HUD, and input events
- `mediapipe` — local hand landmark inference

Voice engines are optional and are not required to start VisionCore. AI uses Python's standard library for its provider transport and is disabled unless configured.

## Installation

```bash
git clone https://github.com/cybervault-Hacky/vision-core.git
cd vision-core
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

On Windows, create the environment with `python -m venv .venv`, activate `.venv\\Scripts\\activate`, and use `python -m pip`.

On a headless Linux host without `libGL.so.1`, MediaPipe may bring in its GUI OpenCV companion. If startup reports that library as unusable, restore the declared headless build with:

```bash
python3 -m pip install --force-reinstall --no-deps 'opencv-python-headless>=4.8.0,<4.12'
```

## Running

The canonical startup path is:

```bash
python3 main.py
```

The application performs its real startup checks in this order:

```text
INITIALIZING → SUBSYSTEM CHECK → CAMERA → TRACKING → VISIONCORE READY
```

`python3 main.py --version` prints `VisionCore v1.0.0` without importing the desktop dependencies. Useful diagnostics are available with:

```bash
python3 main.py --diagnostics
```

For a headless or camera-free validation run, use the supported synthetic source:

```bash
python3 main.py --mock-camera
```

This stream is explicitly marked synthetic and is not a physical-camera validation claim. Other supported options include `--camera-index N`, `--max-hands N`, `--no-tracking`, and `--debug`.

## Controls

| Key | Action |
| --- | --- |
| `ESC` | Shut down and release resources |
| `C` | Enable, pause, or resume mouse control |
| `M` | Select `MOUSE` mode |
| `D` | Select `DEVICE` mode |
| `A` | Open or close the AI panel |
| `V` | Open one voice listening window, or cancel it |
| `P` | Show or hide performance diagnostics |
| `R` | Retry the camera from the recovery screen |
| `F11` | Toggle fullscreen |

Mouse control always starts disabled. `OPEN_PALM` is the deliberate emergency-stop gesture; it releases held mouse buttons and stops control. Shutdown also releases control before the camera, tracker, and window are closed.

## Optional AI configuration

VisionCore runs without an AI provider. In that state the HUD reports `AI / NOT CONFIGURED`, while deterministic questions about VisionCore's own live state can still be answered locally.

To enable an OpenAI-compatible chat-completions provider, set environment variables before starting:

```bash
export VISIONCORE_AI_PROVIDER=openai
export VISIONCORE_AI_API_KEY=your-key-here
export VISIONCORE_AI_MODEL=gpt-4o-mini
# Optional:
export VISIONCORE_AI_BASE_URL=https://api.openai.com/v1
export VISIONCORE_AI_TIMEOUT_SEC=20
export VISIONCORE_AI_MAX_TOKENS=400
export VISIONCORE_AI_TEMPERATURE=0.2
python3 main.py
```

`VISIONCORE_AI_PROVIDER=none` disables the provider. API keys are read from the environment only, are never logged or written to a configuration file, and must never be committed. A configured provider receives only the typed conversation and the small sanitized state block defined by the application; it does not receive camera frames, raw audio, unrelated files, or secrets.

## Optional voice input

Voice is local-only, optional, and off by default. Press `V` or use the microphone control in the AI panel to open one bounded listening window. A second `V`, cancellation, timeout, emergency stop, or shutdown closes it. No wake word, always-on microphone, speech output, or cloud speech path exists.

Supported environment settings are read without storing them:

```bash
export VISIONCORE_SPEECH_PROVIDER=auto   # auto | vosk | sphinx | none
export VISIONCORE_SPEECH_MODEL=/path/to/local/vosk-model  # required for vosk
export VISIONCORE_SPEECH_DEVICE=2        # optional input device index
export VISIONCORE_SPEECH_TIMEOUT_SEC=8
export VISIONCORE_SPEECH_PHRASE_LIMIT_SEC=15
export VISIONCORE_SPEECH_SAMPLE_RATE=16000
```

Install a local engine separately only if voice is needed. Without a usable engine or microphone, the interface reports `VOICE UNAVAILABLE` and the rest of VisionCore remains usable.

## Hardware and capability honesty

The release distinguishes these states:

- **Implemented:** camera capture, local tracking, gesture recognition, safety-gated control layers, optional provider integration, and optional local voice architecture.
- **Simulated / injected:** `--mock-camera` provides a synthetic calibration stream for headless validation. It does not generate hand input and does not prove physical hardware support.
- **Hardware validated:** only hardware tested on the actual target host may be called hardware validated. This repository makes no physical camera, microphone, Windows input, or Linux X11 claim for an environment where that hardware was not present.
- **Unavailable:** missing devices, permissions, display servers, OS APIs, optional voice engines, and provider configuration are shown with their real reason.

The Phase 9 performance baseline was approximately **25.4 FPS at 1280×800** in its validation environment. It is a baseline measurement, not a promise for every machine. The 30-minute certification recorded **17,853 iterations**, **+1.0 MB RSS**, flat thread count, no worker accumulation, and clean shutdown in that validation environment.

## Safety and privacy

VisionCore is local-first. Camera frames and raw microphone samples remain in memory for their bounded processing path and are not uploaded or persisted. There is no telemetry, analytics, crash-reporting service, background network traffic, arbitrary shell execution, `eval`, or `exec`.

The optional AI path is the exception by design: when a user explicitly sends a request to a configured provider, only the documented conversation and sanitized VisionCore state context are sent. AI and voice requests use the existing allowlist and safety gate; they cannot bypass emergency stop, mode, capability, or shutdown rules.

## Limitations

- Live tracking needs a functioning camera, MediaPipe runtime, and suitable lighting.
- Mouse and device capabilities are platform-dependent and may be unavailable in a headless or Wayland-only session.
- Voice needs an installed local engine and a usable microphone; no voice package is a required dependency.
- AI needs a user-supplied provider, model, and API key; it is not required for the core application.
- `--mock-camera` is for synthetic startup and rendering validation only.

## License

VisionCore is released under the [MIT License](LICENSE).
