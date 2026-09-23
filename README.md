# VisionCore

**VisionCore v1.0.0** is a local-first desktop application for camera-based hand tracking, gesture recognition, and explicitly enabled touchless computer control. It combines an on-device vision pipeline with a dark, futuristic HUD, a safety-gated mouse layer, optional device controls, an optional AI assistant, and optional local voice input.

VisionCore reports the real state of each subsystem. A missing camera, microphone, display backend, speech engine, AI provider, or device capability is shown as unavailable instead of being presented as ready.

## Overview

VisionCore is designed to run as one desktop application:

```text
Camera
  ↓
Hand tracking
  ↓
Gesture recognition
  ↓
Safety-gated control layer
  ↓
HUD and feedback
  ↓
Optional AI / local voice input
```

The core vision path runs locally. Camera frames and hand landmarks stay in memory. Voice input is optional, explicitly activated, and local-only. AI is disabled unless the user configures a provider and explicitly sends a request.

VisionCore v1.0.0 is the final release state of the project. No additional roadmap phase is required.

## Features

- Animated startup checks for the camera, tracking, gesture, control, and HUD subsystems.
- Camera capture on a background worker with stale-frame dropping and clean release.
- MediaPipe hand landmark tracking with 21 landmarks, handedness, confidence, and measured pipeline telemetry.
- Geometry-based gesture recognition with temporal stability, release handling, confidence thresholds, and a neutral `NONE` state.
- Opt-in touchless mouse control with pointer movement, click, drag, scrolling, pause, and emergency stop.
- Separate `MOUSE` and `DEVICE` control modes so one gesture cannot operate both layers.
- Platform capability probing for mouse, volume, mute, media, brightness, windows, and the allowlisted application launcher.
- Optional OpenAI-compatible AI assistant with structured parsing and the existing safety gates.
- Optional local voice input through Vosk or offline PocketSphinx integration; microphone access is off by default.
- Honest recovery screens, unavailable states, action feedback, and a bounded shutdown sequence.
- CLI diagnostics for Python, libraries, display state, platform information, and camera availability.

## How It Works

1. `main.py` verifies Python and required dependencies, then loads validated configuration.
2. The application creates the window and begins the boot screen.
3. The camera is opened and its first frame is checked asynchronously.
4. If enabled, the MediaPipe tracker starts on its own worker thread.
5. The gesture engine converts tracked landmarks into stable gesture results without calling operating-system APIs.
6. The mouse or device controller applies mode, capability, confidence, pause, and emergency-stop gates before acting.
7. The HUD renders measured subsystem state, tracking data, gestures, controls, errors, and action feedback.
8. The AI panel and voice input are deliberate input methods. Voice transcripts become ordinary assistant messages and use the same parser, allowlist, and safety path as typed messages.
9. Shutdown releases control first, then device resources, tracking, camera, gesture state, and the window.

## Architecture

The production code is organized into small layers:

- `main.py` — canonical entry point, CLI parsing, Python/dependency checks.
- `version.py` — single source of truth for the release identity.
- `app/application.py` — lifecycle coordinator, frame loop, subsystem synchronization, and shutdown ordering.
- `app/camera.py` — physical and synthetic camera sources, asynchronous capture, frame lifecycle.
- `app/hand_tracking.py` — lazy MediaPipe initialization, tracking worker, landmarks, smoothing, and tracking state.
- `app/gestures/` — normalized landmark features, pose classification, temporal stabilization, swipe detection, and gesture snapshots.
- `app/controls/` — mouse and device controllers, platform backends, safety gates, capability reporting, and the fixed application launcher allowlist.
- `app/interaction/` — intent routing, interaction state, action feedback, and input provenance.
- `app/ai/` — environment configuration, sanitized context, provider transport, strict response parsing, allowlist, and background worker.
- `app/voice/` — optional local speech engine selection, bounded capture, cancellation, stale-result protection, and voice state.
- `ui/` — boot, camera, HUD, gesture, tracking, AI, feedback, and shutdown rendering.
- `utils/` — platform information and dependency/camera diagnostics.

The gesture engine does not control the operating system. AI and voice do not call control backends directly. All actions end in the existing typed controllers and safety gates.

## Requirements

### Python

- Python 3.10 or newer.
- MediaPipe wheel availability depends on the Python interpreter and operating system. The declared range is kept below `0.10.22` because this application uses the bundled `solutions` hand graph.

### Operating system and desktop

The application window is intended for Linux, Windows, and macOS. Actual control support depends on the host:

- Linux mouse control requires an X11 display and XTEST support. A Wayland-only session without XWayland is reported as unavailable.
- Windows mouse and window controls use native Windows APIs.
- Device capabilities are probed individually. Audio tools, a writable backlight device, an X11 window manager, and allowlisted applications may or may not exist.
- macOS can run the application window and local vision pipeline, but the current mouse/device backends report unsupported capabilities rather than pretending to work.
- A working camera is required for live tracking. `--mock-camera` provides a clearly marked synthetic source for rendering and lifecycle validation.

### Required Python packages

`requirements.txt` contains the required runtime packages:

| Package | Purpose |
| --- | --- |
| `opencv-python-headless` | Camera capture, conversion, and resizing |
| `numpy` | Frame buffers and landmark mathematics |
| `pygame` | Desktop window, HUD rendering, and input events |
| `mediapipe` | On-device hand landmark inference |

AI provider transport uses Python's standard library. Voice engines and microphone bindings are optional and are not required to start the application.

## Installation

### Linux and macOS

```bash
git clone https://github.com/cybervault-Hacky/vision-core.git
cd vision-core
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 main.py
```

### Windows

```powershell
git clone https://github.com/cybervault-Hacky/vision-core.git
cd vision-core
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The application itself is always launched through `main.py`; no local server or second process is required.

### Headless Linux and OpenCV

MediaPipe declares `opencv-contrib-python` as a transitive dependency. On a Linux host without `libGL.so.1`, that GUI build can make the otherwise headless OpenCV import fail. VisionCore reports this clearly. Restore the declared headless OpenCV build with:

```bash
python3 -m pip install --force-reinstall --no-deps 'opencv-python-headless>=4.8.0,<4.12'
```

A headless machine also needs a display strategy for Pygame. For validation only, a dummy SDL display can be used:

```bash
SDL_VIDEODRIVER=dummy python3 main.py --mock-camera
```

This does not provide physical camera or hand input and is not a hardware validation claim.

## Running

The canonical command is:

```bash
python3 main.py
```

VisionCore starts with:

```text
INITIALIZING
    ↓
SUBSYSTEM CHECK
    ↓
CAMERA
    ↓
TRACKING
    ↓
VISIONCORE READY
```

The camera and tracking results are asynchronous and real. If either is unavailable, the boot screen and HUD show the unavailable state and provide the appropriate recovery or limitation message.

## Command-Line Options

```text
python3 main.py --help
```

Available options:

| Option | Purpose |
| --- | --- |
| `--help` | Show command-line help and exit. |
| `--version` | Print `VisionCore v1.0.0` and exit without importing desktop dependencies. |
| `--diagnostics` | Print platform, Python, library, display, and camera diagnostics, then exit. |
| `--camera-index N` | Use camera device index `N` instead of the default index `0`. |
| `--mock-camera` | Use the synthetic calibration source instead of physical camera hardware. |
| `--max-hands N` | Track between 1 and 4 hands; the default is 1. |
| `--no-tracking` | Run the camera HUD without starting the hand-tracking pipeline. |
| `--debug` | Enable verbose console logging. |

`VISIONCORE_LOG_FILE=/path/to/file.log python3 main.py --debug` can be used when a user explicitly wants a log file. Runtime logs are not written by default.

## Controls

| Key | Action |
| --- | --- |
| `ESC` | Request shutdown. Control is released before resources are closed. |
| `C` | Enable, pause, or resume mouse control. |
| `M` | Select `MOUSE` mode. |
| `D` | Select `DEVICE` mode. |
| `A` | Open or close the VisionCore AI panel. |
| `V` | Start one bounded voice listening window, or cancel the active one. |
| `P` | Show or hide performance diagnostics. |
| `R` | Retry the camera from the recovery screen. |
| `F11` | Toggle fullscreen. |

Mouse and device control always start disabled. A deliberate interface action is required before either control layer can act.

## Gesture Controls

The gesture engine recognizes these existing gestures:

| Gesture | Meaning | Notes |
| --- | --- | --- |
| `POINT` | Index-finger pointing | Engages mouse pointer movement in `MOUSE` mode. |
| `PINCH` | Thumb and index pinch | Clicks or starts a drag in `MOUSE` mode; toggles mute once per cycle in `DEVICE` mode. |
| `TWO_FINGER` | Index and middle fingers extended | Movement scrolls in `MOUSE` mode; vertical movement controls volume in `DEVICE` mode. |
| `OPEN_PALM` | Open hand | A deliberate hold triggers the emergency stop. In `DEVICE` mode, a brief cycle can play/pause before the longer stop threshold. |
| `FIST` | Closed hand | No mouse action; vertical movement controls brightness only when the device exposes a writable brightness capability. |
| `SWIPE_LEFT` | Leftward temporal motion | Previous track in `DEVICE` mode; no mouse action. |
| `SWIPE_RIGHT` | Rightward temporal motion | Next track in `DEVICE` mode; no mouse action. |
| `NONE` | No stable recognized pose | Neutral state; it cannot trigger a control action. |

A hand must remain sufficiently visible and confident for the relevant controller to act. Hand loss, low confidence, pause, mode changes, and emergency stop release active interactions.

## Mouse Mode

Mouse control is opt-in and requires a working platform mouse backend.

| Gesture | Mouse action |
| --- | --- |
| `POINT` | Move the cursor with the index fingertip inside the configured control region. |
| `POINT` followed by `PINCH` | One left click per pinch cycle. |
| Held `PINCH` | Start a drag after the configured hold interval. Release ends the drag. |
| `TWO_FINGER` plus vertical movement | Scroll after the configured deadzone is crossed. A stationary hand does not scroll. |
| Held `OPEN_PALM` | Emergency stop; active pointer state and held buttons are released. |

Control starts `DISABLED`, not merely idle. The pointer does not move until the user enables it. Mouse buttons are released on pinch release, hand loss, pause, emergency stop, backend failure, and shutdown.

## Device Mode

`DEVICE` mode is a separate control layer. It is enabled only when the device backend reports a capability and the layer is explicitly armed.

| Gesture or action | Device behavior |
| --- | --- |
| `TWO_FINGER` plus vertical movement | Rate-limited volume adjustment when volume is available. |
| `PINCH` | One mute toggle per pinch cycle when mute is available. |
| Brief `OPEN_PALM` | Play/pause when media control is available. |
| `SWIPE_RIGHT` / `SWIPE_LEFT` | Next/previous track when media control is available. |
| `FIST` plus vertical movement | Brightness adjustment only with a writable backlight capability. |
| Held `OPEN_PALM` | Emergency stop for device and mouse control. |
| Device panel buttons | Explicit window actions and allowlisted application launcher entries when supported. |

The HUD shows each capability as `READY` or `UNAVAILABLE` with the platform-provided reason. No device action is run in `MOUSE` mode, and no unsupported action reports success.

## AI Assistant

The AI assistant is optional and is not needed for camera, gestures, mouse, or device control. Press `A` or select the AI panel to activate the interface. A request is sent only after the user submits text.

### Configuration

```bash
export VISIONCORE_AI_PROVIDER=openai
export VISIONCORE_AI_API_KEY=YOUR_API_KEY
export VISIONCORE_AI_MODEL=gpt-4o-mini

# Optional OpenAI-compatible endpoint and limits:
export VISIONCORE_AI_BASE_URL=https://api.openai.com/v1
export VISIONCORE_AI_TIMEOUT_SEC=20
export VISIONCORE_AI_MAX_TOKENS=400
export VISIONCORE_AI_TEMPERATURE=0.2
python3 main.py
```

Supported provider values are `openai`, `openai_compatible`, or `none`. An unknown provider, empty key, invalid URL scheme, or missing model/provider configuration leaves AI unconfigured. The API key is read from the environment only and is not written to `config.json`, logs, UI, or the repository.

### Safety model

AI output is never executed as code. The path is:

```text
provider response
  → strict JSON parsing
  → typed schema
  → closed action allowlist
  → mode and control gates
  → emergency-stop gate
  → platform capability gate
  → existing intent router and controller
```

Unknown actions, malformed responses, provider failures, cancelled requests, stale responses, shell requests, file requests, power requests, and other system changes are refused. Disruptive allowlisted actions require confirmation. AI cannot enable control, bypass emergency stop, access arbitrary files, run commands, or operate on raw camera or microphone data.

With no provider configured, deterministic questions about VisionCore's current state can still receive local answers. These replies are labeled `LOCAL` and are not represented as model output.

## Voice

Voice is optional, local-only, explicitly activated, and off by default. Press `V` or use the microphone button in the AI panel to open one bounded listening window. Press `V` again to cancel it.

Supported settings:

```bash
export VISIONCORE_SPEECH_PROVIDER=auto   # auto | vosk | sphinx | none
export VISIONCORE_SPEECH_MODEL=/path/to/local/vosk-model
export VISIONCORE_SPEECH_DEVICE=2        # optional input device index
export VISIONCORE_SPEECH_TIMEOUT_SEC=8
export VISIONCORE_SPEECH_PHRASE_LIMIT_SEC=15
export VISIONCORE_SPEECH_SAMPLE_RATE=16000
```

`auto` selects an installed local Vosk model or offline PocketSphinx. No speech package is required for normal startup. If no local engine or microphone is usable, the HUD reports `VOICE UNAVAILABLE` and the rest of VisionCore remains usable.

A voice session is bounded, cancellable, and closed on timeout, emergency stop, or shutdown. A transcript is passed to the same AI assistant pipeline as typed text. Raw microphone samples are not saved or sent to an AI provider. VisionCore has no wake word, always-on microphone, or text-to-speech layer.

## Safety

Safety is implemented in the control layers rather than being a UI promise:

- Mouse and device layers start disabled.
- Mode arbitration prevents mouse and device layers from acting on the same gesture.
- Tracking presence, confidence, gesture stability, deadzones, cooldowns, and platform capabilities are checked before actions.
- `OPEN_PALM` held for the configured emergency interval triggers an authoritative stop.
- Emergency stop releases held mouse buttons, cancels continuous device actions, cancels pending AI confirmations, and closes active voice capture.
- A stale AI response or voice transcript cannot execute after cancellation or shutdown.
- Camera loss, hand loss, low confidence, pause, backend failure, and application shutdown release active control.
- Shutdown is idempotent and performs cleanup before displaying the shutdown animation.

## Privacy

VisionCore is local-first:

- Camera frames are processed from memory and are not recorded or uploaded.
- Raw microphone audio is captured only during an explicit local listening window, is not persisted, and is never sent to a speech cloud service.
- Conversations, recent actions, and feedback are held in memory and discarded on exit.
- No telemetry, analytics, tracking beacons, crash-reporting service, or background network activity is included.
- The optional AI provider is contacted only after the user submits a request. It receives the conversation and a deliberately constructed state block containing HUD-visible status, control state, capability state, and recent action labels.
- AI requests do not include camera frames, images, raw audio, filesystem paths, unrelated application data, passwords, or API keys.

## Hardware Support

Capability classification for this release:

| Classification | Meaning |
| --- | --- |
| **Implemented** | The source contains the camera, local tracking, gesture, safety, mouse, device, AI, and voice paths described here. |
| **Simulated / injected** | `--mock-camera` supplies a synthetic calibration frame stream for headless lifecycle and rendering checks. It does not synthesize hand input and does not prove hardware support. |
| **Hardware validated** | Only physical hardware tested on the actual target host qualifies. The release audit environment did not contain a physical camera, microphone, X11 mouse session, or Windows device APIs, so no claim is made for those items here. |
| **Unavailable** | A missing device, permission, display server, optional package, or operating-system capability is reported individually by the application. |

Live tracking requires a camera, a working MediaPipe installation, and suitable lighting. Mouse and device behavior is intentionally platform-dependent; unavailable backends are safer than unsupported emulation.

## Troubleshooting

### Camera unavailable

The boot screen and recovery view report the camera index and failure reason. Check that:

1. The camera is connected and not locked by another application.
2. Operating-system camera permissions allow Python or the terminal to use it.
3. The selected index is correct; try `python3 main.py --camera-index 1`.
4. The camera returns frames, not only an open device handle.
5. You are not using `--mock-camera` when you need physical camera input.

Press `R` or select `RETRY CAMERA` from the recovery view after correcting the issue.

### MediaPipe or OpenCV unavailable

Install the declared dependencies inside the active virtual environment:

```bash
python3 -m pip install -r requirements.txt
python3 -m pip check
```

On headless Linux, follow the OpenCV/libGL remediation in [Headless Linux and OpenCV](#headless-linux-and-opencv). Do not install packages into the system interpreter when it is marked externally managed; use a virtual environment.

### Tracking unavailable

Tracking is disabled by `--no-tracking`, cannot initialize if MediaPipe is unusable, or may be unavailable after a camera failure. The HUD reports the actual reason. No gesture control is possible while tracking is unavailable.

### Voice unavailable

Voice needs an optional local engine, its local model where applicable, a microphone, and permission to open the input device. Check `VISIONCORE_SPEECH_PROVIDER`, `VISIONCORE_SPEECH_MODEL`, and `VISIONCORE_SPEECH_DEVICE`. The microphone is intentionally not opened automatically.

### AI not configured or unavailable

AI is optional. Set `VISIONCORE_AI_PROVIDER`, `VISIONCORE_AI_API_KEY`, and `VISIONCORE_AI_MODEL` only when a provider is intended. Missing configuration, an invalid key, timeout, rate limit, malformed response, or unreachable endpoint is shown as an AI error; the camera and local controls continue independently.

### Mouse backend unavailable

- Linux requires `DISPLAY`, an X11 server or XWayland, `libX11`, and XTEST support. A Wayland-only session without XWayland is reported as unsupported.
- Windows uses `user32` and `SendInput` in a normal desktop session. VisionCore does not bypass permissions or integrity policies.
- Other platforms keep the application usable but report mouse control as unavailable when no backend exists.

### Device capability unavailable

Volume, media, brightness, window, and launcher availability is probed independently. Linux may need `wpctl`, `pactl`, or `amixer`, a writable `/sys/class/backlight` device, an X11 session, or an installed allowlisted application. Missing capabilities are not approximated.

### Display or fullscreen problems

VisionCore requires a desktop display for its normal HUD. Use `--diagnostics` to inspect the display state. `SDL_VIDEODRIVER=dummy` is suitable only for headless validation and does not provide a visible window.

## Performance

The Phase 9 validation baseline was approximately:

```text
25.4 FPS at 1280×800
```

The 30-minute Phase 9 resource certification recorded:

```text
17,853 iterations
RSS increase: +1.0 MB
threads: flat
worker accumulation: none
clean shutdown
```

These are measurements from the validation environment, not a performance guarantee for every camera, desktop, interpreter, or operating system.

## Security

VisionCore contains no arbitrary shell execution path. The existing subprocess calls are fixed-argument helpers for known platform tools, and the application launcher resolves only a frozen allowlist of applications to absolute executable paths. No shell, command interpreter, `eval`, or `exec` is used for user, gesture, voice, or AI input.

The AI parser accepts structured data only. The voice path produces a transcript, not a second operating-system command path. Both terminate in the same allowlist, mode, capability, and safety gates used by the rest of the application.

Users should still protect their own environment variables and provider credentials. Never commit API keys, tokens, passwords, or local environment files.

## Project Structure

```text
vision-core/
├── app/
│   ├── ai/             # Optional provider-backed assistant and safety routing
│   ├── controls/       # Mouse/device controllers and platform backends
│   ├── gestures/       # Landmark geometry and gesture state machine
│   ├── interaction/    # Intent routing and action feedback
│   ├── voice/          # Optional local voice input
│   ├── application.py  # Application coordinator and lifecycle
│   ├── camera.py       # Camera sources and capture worker
│   ├── config.py       # Validated local configuration
│   ├── hand_tracking.py # MediaPipe tracking worker and data model
│   └── state.py        # Telemetry and lifecycle state
├── ui/                 # Boot, HUD, camera, panels, and shutdown screens
├── utils/              # Platform and diagnostics helpers
├── main.py             # Canonical entry point
├── requirements.txt    # Required runtime dependencies
├── version.py          # Central release identity
├── LICENSE             # MIT license
└── README.md           # Project documentation
```

## Development

Use a virtual environment and install the same declared runtime dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m compileall .
python3 main.py --diagnostics
```

The repository intentionally contains production source only; it does not ship a test framework or validation-output directory. Keep local configuration, logs, virtual environments, caches, and validation output outside the tracked source tree. Before a release, review:

```bash
git status
git diff --check
git ls-files
```

Preserve the existing safety boundaries when making changes. New input sources must use the existing typed intent path, and optional dependencies must remain optional at startup.

## Limitations

- Physical camera, microphone, display, and OS control availability depends on the host.
- The synthetic camera is for lifecycle and rendering validation only; it does not generate a hand or gesture stream.
- AI requires a user-configured provider and is not required for the local vision pipeline.
- Voice requires a local speech engine and microphone; there is no cloud speech fallback or text-to-speech output.
- Linux mouse control requires an X11-compatible session. Windows and macOS behavior differs because their native control APIs differ.
- Device actions are limited to capabilities exposed by the host and the fixed allowlist. Unsupported capabilities are not emulated.
- The reported performance and memory values are validation measurements, not universal guarantees.

## License

VisionCore is distributed under the [MIT License](LICENSE).

## Version

**VisionCore v1.0.0**
