# VisionCore

**VisionCore** is a high-performance, local-first computer vision framework designed for touchless device control using standard webcam hardware and real-time computer vision.

It delivers a rock-solid desktop application architecture, a hardware camera capture pipeline, non-blocking asynchronous streaming, on-device hand landmark tracking, a geometry based real-time gesture recognition engine, and a sci-fi inspired AI vision heads-up display (HUD).

> **Device control is not enabled.** VisionCore recognises gestures and visualises them; it does not move the mouse, click, type, change the volume or control media. Gestures currently produce data and HUD feedback only.

---

## Features

* ✓ **Futuristic camera interface**
* ✓ **Local hand tracking** — MediaPipe hand landmark inference (21 landmarks per hand) running entirely on-device, on a dedicated worker thread
* ✓ **Hand landmark visualization** — landmark nodes, bone connections, soft bloom and animated tracking brackets rendered over the live video
* ✓ **Tracking confidence** — the real handedness classification score reported by the tracking engine (never synthesised)
* ✓ **Handedness when available** — `LEFT` / `RIGHT` shown only when the engine returns a valid classification
* ✓ **Real-time tracking HUD** — tracking states (`SEARCHING`, `ACQUIRING`, `LOCKED`, `LOST`), lock-on animation, released-pose fade and live pipeline telemetry
* ✓ **Gesture recognition** — `OPEN_PALM`, `FIST`, `POINT`, `PINCH`, `TWO_FINGER`, `SWIPE_LEFT` and `SWIPE_RIGHT` classified from landmark geometry on-device, with no additional model and no cloud service
* ✓ **Gesture stabilisation** — configurable consecutive-frame validation, release debounce and a neutral `NONE` state so an ambiguous hand never reports a wrong gesture
* ✓ **Pinch detection** — thumb/index tip contact measured relative to palm scale (never raw pixels), with press/hold/release phases
* ✓ **Point detection** — index-dominant pose with folded middle, ring and pinky fingers
* ✓ **Open palm detection** — four-finger extension scoring that tolerates one poorly tracked finger
* ✓ **Fist detection** — curled-finger evidence from every finger, independent of where the hand sits in frame
* ✓ **Two-finger detection** — index and middle extended, ring and pinky folded, kept exclusive from `POINT`
* ✓ **Swipe detection** — temporal left/right swipes from a bounded motion history with distance, velocity, direction-consistency, settle and cooldown checks

* **Futuristic Boot Sequence**: Animated 2.4-second system initialization sequence probing core architecture, display subsystems, and camera hardware before entering active mode.
* **Low-Latency Camera Pipeline**: Asynchronous background capture thread running independently of the UI thread, ensuring stutter-free rendering and zero frame drops.
* **Aspect-Ratio Preserving Viewport**: Dynamic letterboxing/pillarboxing that adapts responsively to window resizing without stretching or distorting camera frames.
* **Sci-Fi Camera HUD**:
  * Original visual identity: deep dark slate backdrop, cool cyan accents, ice-blue telemetry, and neutral typography.
  * Real-time hardware telemetry: resolution, camera FPS, render FPS, device index, and orientation.
  * Live status matrix: truthful subsystem reporting (`VISION CORE: ONLINE`, `CAMERA: ONLINE`, `TRACKING: SEARCHING|ACTIVE|LOST`, `GESTURES: SEARCHING|ACTIVE|DISABLED`, `CONTROL: DISABLED`).
  * Subtle scanning animations: vertical sweeping scanline, rotating circular reticle, live indicator pulse, and sci-fi corner brackets.
* **Gesture Recognition Pipeline**:
  * `CAMERA → HAND TRACKING → LANDMARKS → GESTURE ENGINE → RECOGNISED GESTURE → HUD`.
  * Landmark features only: finger extension is scored from wrist-to-tip reach and PIP joint angles, pinch from tip separation relative to palm scale. Nothing is matched against image templates or hard-coded screen regions.
  * Geometry is normalised for hand size, distance, rotation, position and handedness (left and right hands use the same algorithms).
  * Deterministic priority (`PINCH → POINT → TWO_FINGER → FIST → OPEN_PALM`) resolves overlapping evidence, and a confidence threshold keeps weak evidence in the neutral `NONE` state.
  * Reported confidence is the measured geometric evidence of the selected pose - never a random or hard-coded value.
  * Gesture transitions are explicit: `START`, `ACTIVE`, `RELEASE`, plus a `released` field and `changed` flag for the control layer of a later phase.
  * Gesture HUD: dedicated readout panel, landmark chain highlighting for the fingers that produced the gesture, and a restrained effect per gesture (pinch lock ring, point direction chevrons, two-finger focus lines, open palm radial pulse, fist lock bracket, swipe motion trail).
* **Hand Tracking Pipeline**:
  * Capture → frame → hand tracker → 21 hand landmarks → HUD, with stale frames dropped so latency never accumulates.
  * One Euro landmark smoothing: heavy jitter suppression while the hand is still, almost none while it moves, so the overlay stays glued to the hand.
  * Tracking data model (`HandTrackingResult`: `detected`, `landmarks`, `confidence`, `handedness`, `bounding_box`, `timestamp`) ready for future gesture engines.
  * Multi-hand capable configuration (`max_hands` up to 4); one hand is tracked by default for the best latency.
  * Measured pipeline telemetry only: engine latency, pipeline rate and dropped frames.
* **Resilient Error Recovery**:
  * Automatic detection of camera absence, permission rejections, and hardware locks.
  * Polished user-facing recovery screen with interactive `[ RETRY CAMERA ]` and `[ EXIT SYSTEM ]` controls.
  * Keyboard accelerators (`R` to retry, `ESC` to quit, `F11` for fullscreen).
* **Local Gesture Engine**: Pure-Python geometry over the tracking output - a few hundred floating point operations per hand per frame, no second neural network, no added latency, no image processing.
* **System Diagnostics**: Built-in CLI telemetry inspection (`--diagnostics`) reporting OS, Python version, display server, OpenCV backend, and camera capabilities.
* **Zero Cloud Dependency**: 100% offline, local-first processing. Hand landmarks are computed locally from in-memory frames that are never written to disk, uploaded, recorded or transmitted. No model download, no account and no API key are required.

---

## Requirements

* **Python 3.10 – 3.12** (the MediaPipe hand landmark package used for local tracking publishes wheels for CPython 3.9–3.12)
* Standard USB webcam or integrated camera (camera index 0)
* Operating System:
  * Linux (X11 / Wayland / Headless)
  * Windows 10 / 11
  * macOS (12 Monterey or newer)

### Dependencies

`requirements.txt` pins only what the application genuinely uses:

| Package | Purpose |
| --- | --- |
| `opencv-python-headless` | Camera capture, frame colour conversion and resize |
| `numpy` | Frame buffers and the landmark vector maths (`<2` for the MediaPipe runtime) |
| `pygame` | Desktop window, HUD rendering and input |
| `mediapipe` | On-device hand landmark tracking engine (`0.10.14 – 0.10.21`, models bundled in the package) |

Gesture recognition adds **no new dependency**: it is implemented with the standard library over the landmarks the tracking engine already produces.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/cybervault-Hacky/vision-core.git
cd vision-core
```

### 2. Create and activate a virtual environment

**Linux & macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (Command Prompt):**
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **OpenCV build note** — MediaPipe declares `opencv-contrib-python` (the GUI build), so pip installs it next to the headless build and both provide the same `cv2` module. On Windows, macOS and desktop Linux this is harmless. Only on a headless Linux host without `libGL`, restore the headless build:
> ```bash
> pip install --force-reinstall --no-deps opencv-python-headless
> ```
> `python3 main.py` detects this case and prints the exact command to run.

---

## Running VisionCore

To launch the futuristic vision interface:

```bash
python3 main.py
```

### Additional Command-Line Options

```bash
# Display system diagnostics and camera probe telemetry
python3 main.py --diagnostics

# Run with a specific camera device index
python3 main.py --camera-index 1

# Track two hands instead of one (max 4)
python3 main.py --max-hands 2

# Run the camera HUD without the hand tracking pipeline
python3 main.py --no-tracking

# Launch with synthetic camera calibration stream (for headless or development)
python3 main.py --mock-camera

# Enable verbose debug logging
python3 main.py --debug
```

### Keyboard Controls

| Key | Action |
| --- | --- |
| `ESC` | Shut down VisionCore |
| `F11` | Toggle fullscreen |
| `R` | Reconnect the camera from the recovery screen |

---

## Camera Permissions

When launching VisionCore for the first time, your operating system may prompt you to authorize camera access:

* **macOS**: Go to `System Settings` → `Privacy & Security` → `Camera` and verify that `Terminal` (or your Python IDE) is toggled ON.
* **Windows**: Go to `Settings` → `Privacy & Security` → `Camera`, toggle `Camera access` ON, and ensure `Let desktop apps access your camera` is enabled.
* **Linux**: Ensure your current user belongs to the `video` group:
  ```bash
  sudo usermod -aG video $USER
  ```
  *(Log out and back in for group changes to take effect).*

---

## Troubleshooting

### Camera Hardware Unavailable
* **Symptom**: VisionCore displays the `CAMERA HARDWARE UNAVAILABLE` recovery screen after the boot sequence.
* **Fix**:
  1. Confirm your webcam is securely plugged into an active USB port.
  2. Check if another program (Zoom, Google Meet, OBS, Discord) is currently using the camera. Close conflicting programs and press `[R]` or click `[ RETRY CAMERA ]`.
  3. If your device has multiple cameras (e.g. laptop webcam + external USB webcam), specify the camera index:
     ```bash
     python3 main.py --camera-index 1
     ```

### Permission Denied
* **Symptom**: Logs indicate permission denied or V4L2 device open error.
* **Fix**: Follow the platform-specific instructions in [Camera Permissions](#camera-permissions).

### `libGL.so.1: cannot open shared object file`
* **Symptom**: `python3 main.py` reports that OpenCV is unusable and mentions `libGL`.
* **Fix**: install the system OpenGL library (`sudo apt install -y libgl1`), or restore the headless OpenCV build:
  ```bash
  pip install --force-reinstall --no-deps opencv-python-headless
  ```

### Gesture Not Recognised
* **Symptom**: The `GESTURE ENGINE` panel stays on `SEARCHING` or `ANALYZING` while a hand is tracked.
* **Fix**:
  1. Hold the pose steady for a moment - a gesture must agree for `gesture_stability_frames` consecutive frames (`3` by default) before it is reported. Holding an unambiguous pose is what moves the panel from `ANALYZING` to a gesture name.
  2. Keep the hand at a comfortable distance and angle to the camera; gesture geometry is scale and rotation independent, but a hand seen exactly edge-on (fingers pointing straight at the lens) collapses the 2D landmark projection and is deliberately reported as `NONE` rather than guessed.
  3. Swipes need a deliberate flick: roughly 18% of the frame width inside 0.4 s, mostly horizontal, with the hand settling before the next swipe can fire. Increase or decrease `swipe_distance_threshold` / `swipe_velocity_threshold` to taste.
  4. Raise or lower sensitivity with `gesture_confidence_threshold` in `config.json` (higher = stricter).
* **Note**: `NONE` is a first-class result. An ambiguous hand reports `NONE` on purpose so that no future control action could fire by accident.

### Hand Tracking Not Detecting
* **Symptom**: The HUD stays in `TRACKING: SEARCHING` or the `HAND TRACKING` panel reports `UNAVAILABLE`.
* **Fix**:
  1. Ensure your hand is well lit and fully visible in the frame — avoid strong backlighting.
  2. If the boot log reports a MediaPipe initialisation failure, reinstall dependencies (`pip install -r requirements.txt`).
  3. On low-powered machines, keep the default lite model (`tracking_model_complexity` `0`) and the 640 px inference width.

### Gesture Configuration

Gesture recognition is tuned through `config.json` (all values are validated and clamped on load):

```json
{
  "gesture_enabled": true,
  "gesture_confidence_threshold": 0.62,
  "gesture_stability_frames": 3,
  "gesture_release_frames": 2,
  "pinch_threshold": 0.72,
  "pinch_release_threshold": 0.85,
  "pinch_lift_threshold": 1.25,
  "swipe_distance_threshold": 0.18,
  "swipe_velocity_threshold": 0.60,
  "swipe_cooldown": 0.70,
  "swipe_window_sec": 0.40
}
```

`pinch_threshold` is a ratio (thumb-to-index tip separation divided by palm scale), not a pixel count, so it does not change when the hand moves closer to or further from the camera.

### Running in Headless / CI Environments
* If you are running on a server or remote terminal without an attached physical camera, pass `--mock-camera`:
  ```bash
  python3 main.py --mock-camera
  ```
  This loads the synthetic calibration stream, allowing the full HUD and rendering pipeline to run without physical camera hardware.

---

## Security & Privacy

VisionCore is built upon a strict **local-first** security model:

* **No Cloud Processing**: All image processing and camera frame manipulation occurs entirely in system RAM on your local machine.
* **No Telemetry or Tracking**: The application contains zero analytical beacons, telemetry pings, or usage tracking code.
* **No Network Calls**: No network sockets or external HTTP requests are made during normal camera streaming.
* **Gesture Recognition is Local Geometry**: Recognition is pure arithmetic over landmark coordinates already in memory - it cannot transmit anything and it needs no model download, account or API key.
* **No Device Control**: Gesture results are drawn on screen and nothing else. No input device is touched, no shell command is run and no operating system API for mouse, keyboard, volume or media is imported anywhere in the project.
* **No Account Required**: VisionCore runs directly from your terminal with no sign-ups, accounts, or API keys.

---

## Project Architecture

```text
vision-core/
│
├── main.py                  # Streamlined entry point & CLI parser
├── requirements.txt         # Core runtime dependencies
├── README.md                # Documentation & usage guide
├── LICENSE                  # MIT License
├── .gitignore               # Git exclusion patterns
│
├── app/
│   ├── __init__.py          # Package exports
│   ├── application.py       # Application coordinator & event loop
│   ├── camera.py            # Hardware capture thread & frame manager
│   ├── config.py            # AppConfig dataclass & JSON loader/validator
│   ├── hand_tracking.py     # Hand tracking worker, landmark model & smoothing
│   ├── logger.py            # Clean, formatted console logging
│   └── state.py             # Lifecycle state machine & telemetry models
│
├── app/gestures/
│   ├── __init__.py          # Gesture engine package exports
│   ├── types.py             # Gesture, phases, results & tunable settings
│   ├── features.py          # Landmark geometry: extension, pinch, palm scale
│   ├── classifier.py        # Static pose scoring, priority & selection
│   ├── temporal.py          # Bounded motion history & swipe detection
│   └── engine.py            # Per-hand sessions, stabilisation, transitions
│
├── ui/
│   ├── __init__.py          # UI component exports
│   ├── animations.py        # Reusable easing, rotation, pulse & scanline tweens
│   ├── boot_screen.py       # 6-step futuristic system boot sequence
│   ├── camera_view.py       # Letterboxed camera viewport & overlay HUD
│   ├── gesture_overlay.py   # Gesture effects, motion trail & readout panel
│   ├── hand_overlay.py      # Hand landmark visualization & lock-on animation
│   ├── hud.py               # Top header, subsystem matrix & telemetry panels
│   └── window.py            # Desktop window host, button engine & error screens
│
├── utils/
│   ├── __init__.py          # Utility exports
│   ├── platform.py          # Platform, OS, and permission diagnostics
│   └── diagnostics.py       # System inspector & ASCII report generator
```

---

## Roadmap

Delivered:

* ~~Real-time hand tracking with 21 landmarks per hand~~ (Phase 2)
* ~~Gesture recognition: open palm, fist, point, pinch, two finger, swipe left/right~~ (Phase 3)

Planned capabilities for upcoming releases:

* Touchless desktop control (mouse movement, clicking, scrolling, volume, and media gestures) - **not implemented yet**.
* Gesture profile customization, sensitivity curves, and custom action mapping.
* Depth-aware features that use the landmark `z` estimate once it is reliable enough.

*(Note: the HUD reports `GESTURES` as active because recognition really does run, while `CONTROL` stays `DISABLED` because no device control exists. This is deliberate: the interface never claims capability the application does not have.)*

---

## License

This project is licensed under the [MIT License](LICENSE).
