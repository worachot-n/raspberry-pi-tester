"""
camera_stream.py — Flask MJPEG streamer + I2C LCD status display.

Usage:
    uv run python camera_stream.py

Stream URL: http://<device-ip>:5000
"""

import socket
import threading
import time

from flask import Flask, Response

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
    import io
    CAMERA_AVAILABLE = True
except Exception as e:
    print(f"[CAMERA] Not available: {e}")
    CAMERA_AVAILABLE = False

from lib.lcd_i2c import LcdI2C


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_local_ip() -> str:
    """Return the local LAN IP address (not 127.0.0.1)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "Unavailable"


def init_lcd(address: int = 0x27, bus: int = 1) -> LcdI2C | None:
    try:
        lcd = LcdI2C(bus=bus, address=address, rows=4, cols=16)
        return lcd
    except Exception as e:
        print(f"[LCD] Not available: {e}")
        return None


def update_lcd(lcd: LcdI2C | None, ip: str):
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
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/")
def index():
    ip = get_local_ip()
    return (
        f"<html><body style='margin:0;background:#000'>"
        f"<img src='/stream' style='width:100%;height:100vh;object-fit:contain'>"
        f"</body></html>"
    )


@app.route("/stream")
def stream():
    if stream_output is None:
        return Response("Camera not available", status=503)
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


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

    print(f"[FLASK] Starting on http://0.0.0.0:5000  →  http://{ip}:5000")

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
