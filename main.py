"""
Raspberry Pi 4B  Web Dashboard
Run:  uv run python main.py
Open: http://<device-ip>:5000
"""

import io
import os
import socket
import subprocess
import sys
import threading

import RPi.GPIO as GPIO
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
    CAMERA_AVAILABLE = True
except Exception as e:
    print(f"[CAMERA] Not available: {e}")
    CAMERA_AVAILABLE = False

from lib.lcd_i2c import LcdI2C
from lib.tm1637 import TM1637


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def build_config() -> dict:
    load_dotenv()
    return {
        "relays": [
            int(os.getenv("RELAY_1", 21)),
            int(os.getenv("RELAY_2", 20)),
            int(os.getenv("RELAY_3", 12)),
        ],
        "tm1637": [
            {"clk": int(os.getenv("TM1637_H0_CLK",  4)), "dio": int(os.getenv("TM1637_H0_DIO", 17))},
            {"clk": int(os.getenv("TM1637_H1_CLK", 27)), "dio": int(os.getenv("TM1637_H1_DIO", 22))},
            {"clk": int(os.getenv("TM1637_H2_CLK",  5)), "dio": int(os.getenv("TM1637_H2_DIO",  6))},
            {"clk": int(os.getenv("TM1637_H3_CLK", 13)), "dio": int(os.getenv("TM1637_H3_DIO", 19))},
            {"clk": int(os.getenv("TM1637_H4_CLK", 26)), "dio": int(os.getenv("TM1637_H4_DIO", 16))},
        ],
        "lcd": {
            "address": int(os.getenv("LCD_I2C_ADDRESS", "0x27"), 16),
            "bus":     int(os.getenv("LCD_I2C_BUS", 1)),
            "rows":    int(os.getenv("LCD_ROWS", 4)),
            "cols":    int(os.getenv("LCD_COLS", 16)),
        },
    }


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "Unavailable"


def get_ssid() -> str:
    try:
        result = subprocess.run(
            ["iwgetid", "wlan0", "-r"],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() or "Unknown"
    except Exception:
        return "Unknown"


# ---------------------------------------------------------------------------
# Camera streaming
# ---------------------------------------------------------------------------

class StreamOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame: bytes = b""
        self.condition = threading.Condition()

    def write(self, buf: bytes):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


_stream_output: StreamOutput | None = None
_camera: "Picamera2 | None" = None


def init_camera() -> bool:
    global _camera, _stream_output
    if not CAMERA_AVAILABLE:
        return False
    try:
        _camera = Picamera2()
        cfg = _camera.create_video_configuration(
            main={"size": (1920, 1080), "format": "RGB888"}
        )
        _camera.configure(cfg)
        _stream_output = StreamOutput()
        _camera.start_recording(JpegEncoder(), FileOutput(_stream_output))
        print("[CAMERA] Recording started (1920×1080)")
        return True
    except Exception as e:
        print(f"[CAMERA] Failed: {e}")
        return False


def generate_frames():
    while True:
        if _stream_output is None:
            import time; time.sleep(0.1)
            continue
        with _stream_output.condition:
            _stream_output.condition.wait()
            frame = _stream_output.frame
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )


# ---------------------------------------------------------------------------
# Hardware state
# ---------------------------------------------------------------------------

_config: dict = {}
_relay_states: list[bool] = [False, False, False]   # False = OFF
_displays: list[TM1637] = []
_lcd: LcdI2C | None = None


def setup_gpio():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin in _config["relays"]:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)  # active-low: HIGH = OFF


def set_relay(index: int, on: bool):
    pin = _config["relays"][index]
    GPIO.output(pin, GPIO.LOW if on else GPIO.HIGH)  # active-low
    _relay_states[index] = on


def all_relays_off():
    for i in range(len(_relay_states)):
        set_relay(i, False)


def init_displays():
    global _displays
    _displays = [TM1637(d["clk"], d["dio"]) for d in _config["tm1637"]]
    for d in _displays:
        d.clear()
    print(f"[TM1637] {len(_displays)} displays initialised")


def init_lcd(ip: str, ssid: str):
    global _lcd
    cfg = _config["lcd"]
    try:
        _lcd = LcdI2C(bus=cfg["bus"], address=cfg["address"],
                      rows=cfg["rows"], cols=cfg["cols"])
        _lcd.print_line(0, ssid[:16])
        _lcd.print_line(1, ssid[16:32] if len(ssid) > 16 else "")
        _lcd.print_line(2, "http://")
        _lcd.print_line(3, f"{ip}:5000")
        print(f"[LCD] Showing SSID={ssid!r}  URL={ip}:5000")
    except Exception as e:
        print(f"[LCD] Not available: {e}")
        _lcd = None


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pi Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#111;color:#eee;font-family:system-ui,sans-serif;
        padding:16px;max-width:1400px;margin-inline:auto}}
  h1{{font-size:1.2rem;margin-bottom:12px;color:#adf}}

  /* ── status bar ── */
  .status-bar{{background:#1a1a2e;border-radius:8px;padding:10px 16px;
               margin-bottom:16px;display:flex;flex-wrap:wrap;gap:16px;
               font-size:.85rem;color:#8af}}
  .status-bar strong{{color:#adf}}

  /* ── two-column grid ── */
  .layout{{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}}

  /* ── cards ── */
  .card{{background:#1e1e1e;border-radius:10px;padding:16px;margin-bottom:16px}}
  .card:last-child{{margin-bottom:0}}
  .card h2{{font-size:.95rem;color:#adf;margin-bottom:12px}}

  /* ── camera ── */
  #cam{{width:100%;border-radius:6px;display:block;background:#000}}

  /* ── relays ── */
  .relay-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
  .relay-btn{{padding:12px 0;border:none;border-radius:8px;font-size:.9rem;
              font-weight:600;cursor:pointer;transition:background .15s;width:100%}}
  .relay-btn.off{{background:#333;color:#aaa}}
  .relay-btn.on{{background:#22c55e;color:#fff}}

  /* ── tm1637 ── */
  .tm-row{{display:flex;gap:10px}}
  .tm-row input{{flex:1;padding:10px;border-radius:8px;border:1px solid #444;
                 background:#2a2a2a;color:#eee;font-size:1.1rem;text-align:center}}
  .tm-row button{{padding:10px 20px;border:none;border-radius:8px;background:#3b82f6;
                  color:#fff;font-size:.9rem;font-weight:600;cursor:pointer}}
  .tm-row button:active{{background:#2563eb}}
  #tm-status{{font-size:.8rem;color:#6af;margin-top:6px;min-height:1em}}

  /* ── reset ── */
  .reset-btn{{width:100%;padding:12px;border:none;border-radius:8px;
              background:#dc2626;color:#fff;font-size:.95rem;font-weight:600;
              cursor:pointer;transition:background .15s}}
  .reset-btn:active{{background:#b91c1c}}

  /* ── mobile: single column, info → camera → controls ── */
  @media (max-width:767px) {{
    .layout{{grid-template-columns:1fr}}
    .col-left{{order:1}}
    .col-right{{order:2}}
  }}
</style>
</head>
<body>
<h1>Raspberry Pi Dashboard</h1>

<header class="status-bar">
  <span>SSID: <strong>{ssid}</strong></span>
  <span>URL: <strong>http://{ip}:5000</strong></span>
</header>

<main class="layout">

  <!-- LEFT: camera -->
  <section class="col-left">
    <div class="card">
      <h2>Camera</h2>
      <img id="cam" src="/stream" alt="Camera stream">
    </div>
  </section>

  <!-- RIGHT: controls -->
  <section class="col-right">

    <div class="card">
      <h2>Relays</h2>
      <div class="relay-grid">
        <button class="relay-btn off" id="r0" onclick="toggleRelay(0)">Relay 1<br><small>OFF</small></button>
        <button class="relay-btn off" id="r1" onclick="toggleRelay(1)">Relay 2<br><small>OFF</small></button>
        <button class="relay-btn off" id="r2" onclick="toggleRelay(2)">Relay 3<br><small>OFF</small></button>
      </div>
    </div>

    <div class="card">
      <h2>TM1637 Displays (all 5)</h2>
      <div class="tm-row">
        <input type="number" id="tm-input" min="0" max="9999" placeholder="0 – 9999">
        <button onclick="setDisplays()">Set</button>
      </div>
      <div id="tm-status"></div>
    </div>

    <div class="card">
      <h2>Reset</h2>
      <button class="reset-btn" onclick="resetAll()">Reset All (relays OFF + displays blank)</button>
    </div>

  </section>
</main>

<script>
const states = [false, false, false];

function updateRelayBtn(idx, on) {{
  states[idx] = on;
  const btn = document.getElementById('r' + idx);
  btn.className = 'relay-btn ' + (on ? 'on' : 'off');
  btn.innerHTML = `Relay ${{idx + 1}}<br><small>${{on ? 'ON' : 'OFF'}}</small>`;
}}

async function toggleRelay(idx) {{
  const on = !states[idx];
  const res = await fetch('/relay', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{relay: idx, state: on ? 'on' : 'off'}}),
  }});
  if (res.ok) updateRelayBtn(idx, on);
}}

async function setDisplays() {{
  const raw = document.getElementById('tm-input').value.trim();
  const num = parseInt(raw, 10);
  const status = document.getElementById('tm-status');
  if (isNaN(num) || num < 0 || num > 9999) {{
    status.textContent = 'Enter a number between 0 and 9999.';
    return;
  }}
  const res = await fetch('/tm1637', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{number: num}}),
  }});
  status.textContent = res.ok ? `Showing ${{num}} on all displays.` : 'Error setting displays.';
}}

async function resetAll() {{
  const res = await fetch('/reset', {{method: 'POST'}});
  if (res.ok) {{
    for (let i = 0; i < 3; i++) updateRelayBtn(i, false);
    document.getElementById('tm-input').value = '';
    document.getElementById('tm-status').textContent = 'Reset — all relays OFF, displays blank.';
  }}
}}

document.getElementById('tm-input').addEventListener('keydown', e => {{
  if (e.key === 'Enter') setDisplays();
}});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return _HTML.format(ssid=_ssid, ip=_ip)


@app.route("/stream")
def stream():
    if _stream_output is None:
        return Response("Camera not available", status=503)
    return Response(generate_frames(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/relay", methods=["POST"])
def relay():
    data = request.get_json(force=True)
    idx   = int(data.get("relay", -1))
    state = str(data.get("state", "off")).lower()
    if idx not in (0, 1, 2):
        return jsonify(error="relay must be 0, 1 or 2"), 400
    set_relay(idx, state == "on")
    print(f"[RELAY] {idx} → {'ON' if state == 'on' else 'OFF'}")
    return jsonify(relay=idx, state=state)


@app.route("/tm1637", methods=["POST"])
def tm1637():
    data = request.get_json(force=True)
    try:
        num = int(data.get("number", 0))
    except (ValueError, TypeError):
        return jsonify(error="number must be an integer"), 400
    if not (0 <= num <= 9999):
        return jsonify(error="number out of range 0-9999"), 400
    for d in _displays:
        d.show_number(num)
    print(f"[TM1637] Showing {num} on all displays")
    return jsonify(number=num)


@app.route("/status")
def status():
    return jsonify(relays=_relay_states)


@app.route("/reset", methods=["POST"])
def reset():
    all_relays_off()
    for d in _displays:
        d.clear()
    print("[RESET] All relays OFF, displays blank")
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global _config, _ssid, _ip

    _config = build_config()
    _ip     = get_local_ip()
    _ssid   = get_ssid()

    print(f"[NET]  IP={_ip}  SSID={_ssid}")

    setup_gpio()
    init_displays()
    init_lcd(_ip, _ssid)
    init_camera()

    print(f"[FLASK] http://0.0.0.0:5000  →  http://{_ip}:5000")

    try:
        app.run(host="0.0.0.0", port=5000, threaded=True)
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted.")
    finally:
        all_relays_off()
        for d in _displays:
            try:
                d.clear()
                d.cleanup()
            except Exception:
                pass
        if _lcd is not None:
            try:
                _lcd.cleanup()
            except Exception:
                pass
        if _camera is not None:
            try:
                _camera.stop_recording()
                _camera.close()
            except Exception:
                pass
        GPIO.cleanup()
        print("[MAIN] Cleaned up.")


if __name__ == "__main__":
    main()
