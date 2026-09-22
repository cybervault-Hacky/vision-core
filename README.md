# VisionCore

**VisionCore** is a high-performance, local-first computer vision framework designed for touchless device control using standard webcam hardware and real-time computer vision.

It delivers a rock-solid desktop application architecture, a hardware camera capture pipeline, non-blocking asynchronous streaming, on-device hand landmark tracking, and a sci-fi inspired AI vision heads-up display (HUD).

---

## Features

* ✓ **Futuristic camera interface**
* ✓ **Local hand tracking** — MediaPipe hand landmark inference (21 landmarks per hand) running entirely on-device, on a dedicated worker thread
* ✓ **Hand landmark visualization** — landmark nodes, bone connections, soft bloom and animated tracking brackets rendered over the live video
* ✓ **Tracking confidence** — the real handedness classification score reported by the tracking engine (never synthesised)
* ✓ **Handedness when available** — `LEFT` / `RIGHT` shown only when the engine returns a valid classification
* ✓ **Real-time tracking HUD** — tracking states (`SEARCHING`, `ACQUIRING`, `LOCKED`, `LOST`), lock-on animation, released-pose fade and live pipeline telemetry

* **Futuristic Boot Sequence**: Animated 2.4-second system initialization sequence probing core architecture, display subsystems, and camera hardware before entering active mode.
* **Low-Latency Camera Pipeline**: Asynchronous background capture thread running independently of the UI thread, ensuring stutter-free rendering and zero frame drops.
* **Aspect-Ratio Preserving Viewport**: Dynamic letterboxing/pillarboxing that adapts responsively to window resizing without stretching or distorting camera frames.
* **Sci-Fi Camera HUD**:
  * Original visual identity: deep dark slate backdrop, cool cyan accents, ice-blue telemetry, and neutral typography.
  * Real-time hardware telemetry: resolution, camera FPS, render FPS, device index, and orientation.
  * Live status matrix: truthful subsystem reporting (`VISION CORE: ONLINE`, `CAMERA: ONLINE`, `TRACKING: SEARCHING|ACTIVE|LOST`, `GESTURES: STANDBY`, `CONTROL: DISABLED`).
  * Subtle scanning animations: vertical sweeping scanline, rotating circular reticle, live indicator pulse, and sci-fi corner brackets.
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

### Hand Tracking Not Detecting
* **Symptom**: The HUD stays in `TRACKING: SEARCHING` or the `HAND TRACKING` panel reports `UNAVAILABLE`.
* **Fix**:
  1. Ensure your hand is well lit and fully visible in the frame — avoid strong backlighting.
  2. If the boot log reports a MediaPipe initialisation failure, reinstall dependencies (`pip install -r requirements.txt`).
  3. On low-powered machines, keep the default lite model (`tracking_model_complexity` `0`) and the 640 px inference width.

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
├── ui/
│   ├── __init__.py          # UI component exports
│   ├── animations.py        # Reusable easing, rotation, pulse & scanline tweens
│   ├── boot_screen.py       # 5-step futuristic system boot sequence
│   ├── camera_view.py       # Letterboxed camera viewport & overlay HUD
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

Planned capabilities for upcoming releases:

* Dynamic gesture recognition (pinch, swipe, point, fist, open palm) built on the existing tracking data model.
* Touchless desktop control (mouse movement, clicking, scrolling, volume, and media gestures).
* Gesture profile customization, sensitivity curves, and custom action mapping.

*(Note: Gesture recognition and device control are intentionally in STANDBY / DISABLED and reported as such by the HUD. Real-time hand tracking is active.)*

---

## License

This project is licensed under the [MIT License](LICENSE).
