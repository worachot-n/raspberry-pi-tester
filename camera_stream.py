"""
camera_stream.py — Flask MJPEG streamer + button-triggered dataset capture.

Usage:
    uv run python camera_stream.py

Stream URL: http://<device-ip>:5000
"""

import io
import os
import socket
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, send_file

load_dotenv()

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
    CAMERA_AVAILABLE = True
except Exception as e:
    print(f"[CAMERA] Not available: {e}")
    CAMERA_AVAILABLE = False

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except Exception as e:
    print(f"[GPIO] Not available: {e}")
    GPIO_AVAILABLE = False

from lib.lcd_i2c import LcdI2C

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BUTTON_PIN   = int(os.getenv("BUTTON_PIN", 24))
DEBOUNCE_MS  = int(os.getenv("DEBOUNCE_MS", 50))
DATASET_DIR  = Path(os.getenv("DATASET_DIR", "dataset"))
_TZ          = timezone(timedelta(hours=7))  # UTC+7 Bangkok

DATASET_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "Unavailable"


def init_lcd(address: int = 0x27, bus: int = 1) -> "LcdI2C | None":
    try:
        lcd = LcdI2C(bus=bus, address=address, rows=4, cols=16)
        return lcd
    except Exception as e:
        print(f"[LCD] Not available: {e}")
        return None


def update_lcd(lcd: "LcdI2C | None", ip: str):
    if lcd is None:
        return
    try:
        lcd.print_line(0, "Camera System")
        lcd.print_line(1, "Status: Running")
        lcd.print_line(2, "IP Address:")
        lcd.print_line(3, ip)
    except Exception as e:
        print(f"[LCD] Write error: {e}")


# ---------------------------------------------------------------------------
# MJPEG streaming
# ---------------------------------------------------------------------------

class StreamOutput(io.BufferedIOBase):
    """Thread-safe single-frame buffer for MJPEG."""

    def __init__(self):
        self.frame: bytes = b""
        self.condition = threading.Condition()

    def write(self, buf: bytes):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


stream_output: StreamOutput | None = None
camera: "Picamera2 | None" = None

# Tracks the filename of the most recently captured image
latest_capture: str | None = None
capture_lock = threading.Lock()


def init_camera(width: int = 1920, height: int = 1080) -> bool:
    global camera, stream_output
    if not CAMERA_AVAILABLE:
        return False
    try:
        camera = Picamera2()
        config = camera.create_video_configuration(
            main={"size": (width, height), "format": "RGB888"}
        )
        camera.configure(config)
        stream_output = StreamOutput()
        camera.start_recording(JpegEncoder(), FileOutput(stream_output))
        print(f"[CAMERA] Recording started ({width}x{height})")
        return True
    except Exception as e:
        print(f"[CAMERA] Failed to start: {e}")
        return False


def generate_frames():
    """Yield MJPEG frames from the stream buffer."""
    while True:
        if stream_output is None:
            time.sleep(0.1)
            continue
        with stream_output.condition:
            stream_output.condition.wait()
            frame = stream_output.frame
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )


# ---------------------------------------------------------------------------
# Dataset capture
# ---------------------------------------------------------------------------

def capture_image() -> str | None:
    """Save the current frame to the dataset folder. Returns filename or None."""
    global latest_capture
    if stream_output is None:
        print("[CAPTURE] No stream available.")
        return None
    with stream_output.condition:
        frame = stream_output.frame
    if not frame:
        print("[CAPTURE] Empty frame, skipping.")
        return None

    ts       = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S_%f")[:21]
    filename = f"capture_{ts}.jpg"
    path     = DATASET_DIR / filename
    path.write_bytes(frame)
    with capture_lock:
        latest_capture = filename
    print(f"[CAPTURE] Saved {path}")
    return filename


# ---------------------------------------------------------------------------
# Button listener
# ---------------------------------------------------------------------------

def button_listener():
    """Background thread: watch GPIO button and trigger capture on press."""
    if not GPIO_AVAILABLE:
        print("[BTN] GPIO not available — button capture disabled.")
        return

    debounce_s = DEBOUNCE_MS / 1000.0
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    print(f"[BTN] Listening on GPIO {BUTTON_PIN} (pull-up, active LOW)")

    try:
        while True:
            # Wait for press (active LOW)
            if GPIO.input(BUTTON_PIN) == GPIO.LOW:
                time.sleep(debounce_s)
                if GPIO.input(BUTTON_PIN) == GPIO.LOW:
                    capture_image()
                    # Wait for release before re-arming
                    while GPIO.input(BUTTON_PIN) == GPIO.LOW:
                        time.sleep(0.01)
                    time.sleep(debounce_s)
            time.sleep(0.005)
    except Exception as e:
        print(f"[BTN] Listener error: {e}")
    finally:
        GPIO.cleanup([BUTTON_PIN])


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

_HTML_HEAD = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Camera System</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #111; color: #eee; font-family: sans-serif; }
  header { padding: 12px 20px; background: #1a1a1a; border-bottom: 1px solid #333;
           display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 1.1rem; }
  .badge { font-size: 0.75rem; padding: 2px 8px; border-radius: 4px;
           background: #2a6; color: #fff; }
  .layout { display: grid; grid-template-columns: 1fr 340px;
            gap: 12px; padding: 12px; height: calc(100vh - 50px); }
  .stream-box { position: relative; }
  .stream-box img { width: 100%; height: 100%; object-fit: contain;
                    background: #000; border-radius: 6px; }
  .sidebar { overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
  .sidebar h2 { font-size: 0.85rem; color: #aaa; padding: 4px 0; }
  .gallery { display: flex; flex-direction: column; gap: 8px; }
  .thumb { position: relative; border-radius: 4px; overflow: hidden;
           border: 1px solid #333; cursor: pointer; }
  .thumb img { width: 100%; display: block; }
  .thumb .label { position: absolute; bottom: 0; left: 0; right: 0;
                  background: rgba(0,0,0,.6); font-size: 0.65rem;
                  padding: 3px 6px; white-space: nowrap; overflow: hidden;
                  text-overflow: ellipsis; }
  .thumb:hover { border-color: #4af; }
  .no-captures { color: #555; font-size: 0.85rem; padding: 8px 0; }
  @media (max-width: 700px) {
    .layout { grid-template-columns: 1fr; }
    .sidebar { max-height: 30vh; }
  }
</style>
</head>"""


@app.route("/")
def index():
    captures = sorted(DATASET_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)

    thumbs_html = ""
    if captures:
        for p in captures[:30]:  # show latest 30
            thumbs_html += (
                f'<div class="thumb" onclick="window.open(\'/dataset/{p.name}\')">'
                f'<img src="/dataset/{p.name}" loading="lazy">'
                f'<span class="label">{p.name}</span>'
                f'</div>'
            )
    else:
        thumbs_html = '<p class="no-captures">No captures yet.<br>Press the button to capture.</p>'

    total = len(captures)
    return (
        _HTML_HEAD
        + f"""<body>
<header>
  <h1>Camera System</h1>
  <span class="badge">LIVE</span>
  <span style="font-size:.8rem;color:#aaa;margin-left:auto">
    GPIO {BUTTON_PIN} · {total} capture{'s' if total != 1 else ''} · 1920×1080
  </span>
</header>
<div class="layout">
  <div class="stream-box">
    <img src="/stream" alt="Live stream">
  </div>
  <div class="sidebar">
    <h2>CAPTURED IMAGES</h2>
    <div class="gallery" id="gallery">{thumbs_html}</div>
  </div>
</div>
<script>
  // Refresh gallery every 3 seconds to pick up new captures
  setInterval(() => {{
    fetch('/gallery_fragment').then(r => r.text()).then(html => {{
      document.getElementById('gallery').innerHTML = html;
    }});
  }}, 3000);
</script>
</body></html>"""
    )


@app.route("/gallery_fragment")
def gallery_fragment():
    """Returns just the gallery thumbnails HTML for polling updates."""
    captures = sorted(DATASET_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not captures:
        return '<p class="no-captures">No captures yet.<br>Press the button to capture.</p>'
    html = ""
    for p in captures[:30]:
        html += (
            f'<div class="thumb" onclick="window.open(\'/dataset/{p.name}\')">'
            f'<img src="/dataset/{p.name}" loading="lazy">'
            f'<span class="label">{p.name}</span>'
            f'</div>'
        )
    return html


@app.route("/stream")
def stream():
    if stream_output is None:
        return Response("Camera not available", status=503)
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/dataset/<filename>")
def serve_capture(filename: str):
    path = DATASET_DIR / filename
    if not path.exists() or not path.is_file():
        return Response("Not found", status=404)
    return send_file(str(path), mimetype="image/jpeg")


@app.route("/capture", methods=["POST"])
def api_capture():
    """Manual capture via HTTP POST (e.g. curl -X POST http://pi:5000/capture)."""
    fname = capture_image()
    if fname:
        return {"status": "ok", "file": fname}, 200
    return {"status": "error", "message": "No frame available"}, 503


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ip = get_local_ip()
    print(f"[NET] Local IP: {ip}")

    lcd = init_lcd(address=0x27, bus=1)
    update_lcd(lcd, ip)

    cam_ok = init_camera(width=1920, height=1080)
    if not cam_ok:
        print("[CAMERA] Streaming endpoint will return 503.")

    # Start button listener in background thread
    btn_thread = threading.Thread(target=button_listener, daemon=True)
    btn_thread.start()

    print(f"[FLASK] Starting on http://0.0.0.0:5000  →  http://{ip}:5000")
    print(f"[INFO] Dataset saved to: {DATASET_DIR.resolve()}")

    try:
        app.run(host="0.0.0.0", port=5000, threaded=True)
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted.")
    finally:
        if camera is not None:
            try:
                camera.stop_recording()
                camera.close()
                print("[CAMERA] Stopped.")
            except Exception:
                pass
        if lcd is not None:
            lcd.cleanup()
            print("[LCD] Cleaned up.")


if __name__ == "__main__":
    main()
