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
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import RPi.GPIO as GPIO
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_file

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

DATASET_DIR = Path(os.getenv("DATASET_DIR", "dataset"))
DATASET_DIR.mkdir(exist_ok=True)


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
        "button_pin": int(os.getenv("BUTTON_PIN", 24)),
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
            main={"size": (4608, 2592), "format": "RGB888"},
            controls={"FrameDurationLimits": (100000, 100000)},  # 10 fps
        )
        _camera.configure(cfg)
        _stream_output = StreamOutput()
        _camera.start_recording(JpegEncoder(), FileOutput(_stream_output))
        print("[CAMERA] Recording started (4608×2592 @ 10fps)")
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

_TZ = timezone(timedelta(hours=7))  # UTC+7 Bangkok

_config: dict = {}
_relay_states: list[bool] = [False, False, False]   # False = OFF
_displays: list[TM1637] = []
_lcd: LcdI2C | None = None
_button_state: dict = {"pressed": False, "count": 0, "last_ts": "—"}


def capture_image() -> str | None:
    """Save the current camera frame to DATASET_DIR. Returns filename or None."""
    if _stream_output is None:
        return None
    with _stream_output.condition:
        frame = _stream_output.frame
    if not frame:
        return None
    ts       = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S_%f")[:21]
    filename = f"capture_{ts}.jpg"
    (DATASET_DIR / filename).write_bytes(frame)
    print(f"[CAPTURE] Saved dataset/{filename}")
    return filename


def _button_monitor():
    pin = _config["button_pin"]
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    was_pressed = False
    while True:
        pressed = GPIO.input(pin) == GPIO.LOW   # active-low (pull-up → GND)
        if pressed and not was_pressed:
            time.sleep(0.02)                    # 20 ms debounce
            if GPIO.input(pin) == GPIO.LOW:
                _button_state["pressed"] = True
                _button_state["count"]  += 1
                _button_state["last_ts"] = datetime.now(_TZ).strftime("%H:%M:%S")
                capture_image()
        elif not pressed and was_pressed:
            _button_state["pressed"] = False
        was_pressed = pressed
        time.sleep(0.01)


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

_DATASET_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dataset — Pi Dashboard</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#111;color:#eee;font-family:system-ui,sans-serif;padding:16px;max-width:1600px;margin-inline:auto}
  header{display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap}
  header h1{font-size:1.1rem;color:#adf}
  .back-btn{padding:6px 14px;border:none;border-radius:6px;background:#2a2a2a;color:#adf;
             font-size:.85rem;cursor:pointer;text-decoration:none}
  .back-btn:hover{background:#333}
  .count{font-size:.85rem;color:#888;margin-left:auto}
  /* toolbar */
  .toolbar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;align-items:center}
  .toolbar button{padding:7px 16px;border:none;border-radius:6px;font-size:.85rem;font-weight:600;cursor:pointer}
  .btn-sel-all{background:#2a2a2a;color:#eee}
  .btn-sel-all:hover{background:#3a3a3a}
  .btn-del{background:#dc2626;color:#fff}
  .btn-del:hover{background:#b91c1c}
  .btn-del:disabled{background:#444;color:#777;cursor:default}
  .sel-count{font-size:.85rem;color:#aaa;padding:0 4px}
  /* grid */
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
  .thumb{position:relative;border-radius:8px;overflow:hidden;border:2px solid #2a2a2a;
          cursor:pointer;transition:border-color .15s,transform .1s}
  .thumb:hover{border-color:#4af;transform:scale(1.02)}
  .thumb.selected{border-color:#facc15}
  .thumb img{width:100%;display:block;aspect-ratio:16/9;object-fit:cover;background:#000}
  .thumb .label{padding:5px 8px;font-size:.65rem;color:#aaa;white-space:nowrap;
                overflow:hidden;text-overflow:ellipsis;background:#1a1a1a}
  /* checkbox in corner */
  .cb-wrap{position:absolute;top:6px;right:6px;z-index:2}
  .cb-wrap input[type=checkbox]{width:18px;height:18px;cursor:pointer;accent-color:#facc15}
  /* no captures */
  .empty{color:#555;padding:40px 0;text-align:center;font-size:1rem}
  /* lightbox */
  #lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:100;
      align-items:center;justify-content:center;flex-direction:column;gap:10px}
  #lb.open{display:flex}
  #lb img{max-width:95vw;max-height:88vh;border-radius:6px;object-fit:contain}
  #lb-name{color:#aaa;font-size:.8rem}
  #lb-close{position:absolute;top:14px;right:18px;font-size:1.6rem;background:none;
             border:none;color:#eee;cursor:pointer;line-height:1}
  #lb-close:hover{color:#f66}
  #lb-prev,#lb-next{position:absolute;top:50%;transform:translateY(-50%);
                     background:rgba(255,255,255,.1);border:none;border-radius:50%;
                     width:42px;height:42px;font-size:1.4rem;color:#eee;cursor:pointer}
  #lb-prev{left:12px} #lb-next{right:12px}
  #lb-prev:hover,#lb-next:hover{background:rgba(255,255,255,.25)}
</style>
</head>
<body>
<header>
  <a class="back-btn" href="/">&#8592; Dashboard</a>
  <h1>Dataset</h1>
  <span class="count" id="total-count"></span>
</header>
<div class="toolbar">
  <button class="btn-sel-all" onclick="toggleSelectAll()">Select All</button>
  <span class="sel-count" id="sel-count">0 selected</span>
  <button class="btn-del" id="del-btn" onclick="deleteSelected()" disabled>Delete Selected</button>
</div>
<div class="grid" id="grid"></div>
<p class="empty" id="empty" style="display:none">No captures yet. Press the button to capture an image.</p>

<!-- Lightbox -->
<div id="lb">
  <button id="lb-close" onclick="closeLightbox()">&#x2715;</button>
  <button id="lb-prev" onclick="lbNav(-1)">&#8249;</button>
  <img id="lb-img" src="" alt="">
  <span id="lb-name"></span>
  <button id="lb-next" onclick="lbNav(1)">&#8250;</button>
</div>

<script>
let files = [];
let lbIdx = 0;

async function load() {
  const r = await fetch('/dataset/list');
  files = await r.json();
  render();
}

function render() {
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  document.getElementById('total-count').textContent = files.length + ' capture' + (files.length !== 1 ? 's' : '');
  if (!files.length) { grid.innerHTML = ''; empty.style.display = ''; return; }
  empty.style.display = 'none';
  grid.innerHTML = files.map((f, i) =>
    `<div class="thumb" id="th-${i}" onclick="thumbClick(event,${i})">
      <div class="cb-wrap"><input type="checkbox" id="cb-${i}" onclick="cbClick(event,${i})"></div>
      <img src="/dataset/img/${f}" loading="lazy" alt="${f}">
      <div class="label">${f}</div>
    </div>`
  ).join('');
  updateSelCount();
}

function thumbClick(e, i) {
  // If click was directly on the checkbox, let cbClick handle it
  if (e.target.type === 'checkbox') return;
  openLightbox(i);
}

function cbClick(e, i) {
  e.stopPropagation();
  document.getElementById('th-' + i).classList.toggle('selected', e.target.checked);
  updateSelCount();
}

function updateSelCount() {
  const n = document.querySelectorAll('.cb-wrap input:checked').length;
  document.getElementById('sel-count').textContent = n + ' selected';
  document.getElementById('del-btn').disabled = n === 0;
}

function toggleSelectAll() {
  const boxes = document.querySelectorAll('.cb-wrap input');
  const allChecked = [...boxes].every(b => b.checked);
  boxes.forEach((b, i) => {
    b.checked = !allChecked;
    document.getElementById('th-' + i).classList.toggle('selected', !allChecked);
  });
  updateSelCount();
}

async function deleteSelected() {
  const selected = [...document.querySelectorAll('.cb-wrap input:checked')]
    .map(b => files[parseInt(b.id.replace('cb-',''))]);
  if (!selected.length) return;
  if (!confirm('Delete ' + selected.length + ' image(s)?')) return;
  const r = await fetch('/dataset/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({files: selected}),
  });
  if (r.ok) await load();
}

// Lightbox
function openLightbox(i) {
  lbIdx = i;
  document.getElementById('lb-img').src = '/dataset/img/' + files[i];
  document.getElementById('lb-name').textContent = files[i];
  document.getElementById('lb').classList.add('open');
}
function closeLightbox() { document.getElementById('lb').classList.remove('open'); }
function lbNav(dir) {
  lbIdx = (lbIdx + dir + files.length) % files.length;
  document.getElementById('lb-img').src = '/dataset/img/' + files[lbIdx];
  document.getElementById('lb-name').textContent = files[lbIdx];
}
document.addEventListener('keydown', e => {
  if (!document.getElementById('lb').classList.contains('open')) return;
  if (e.key === 'Escape') closeLightbox();
  if (e.key === 'ArrowLeft') lbNav(-1);
  if (e.key === 'ArrowRight') lbNav(1);
});

load();
</script>
</body>
</html>
"""

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
  .cam-wrap{{position:relative;background:#000;border-radius:6px;overflow:hidden}}
  #cam{{width:100%;display:block;transform:rotate(180deg)}}
  .fullscreen-btn{{position:absolute;top:8px;right:8px;padding:6px 10px;
                   border:none;border-radius:6px;background:rgba(0,0,0,.55);
                   color:#fff;font-size:.8rem;cursor:pointer;backdrop-filter:blur(4px);
                   transition:background .15s}}
  .fullscreen-btn:hover{{background:rgba(255,255,255,.2)}}

  /* ── relays ── */
  .relay-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
  .relay-btn{{padding:10px 0 6px;border:none;border-radius:8px;font-size:.9rem;
              font-weight:600;cursor:pointer;transition:background .15s;width:100%}}
  .relay-btn.off{{background:#333;color:#aaa}}
  .relay-btn.on{{background:#22c55e;color:#fff}}
  .relay-indicator{{display:inline-block;width:10px;height:10px;border-radius:50%;
                    margin-bottom:4px;background:#555;transition:background .2s}}
  .relay-btn.on  .relay-indicator{{background:#86efac;box-shadow:0 0 6px #22c55e}}
  .relay-btn.off .relay-indicator{{background:#555}}

  /* ── tm1637 ── */
  .tm-row{{display:flex;gap:10px}}
  .tm-row input{{flex:1;padding:10px;border-radius:8px;border:1px solid #444;
                 background:#2a2a2a;color:#eee;font-size:1.1rem;text-align:center}}
  .tm-row button{{padding:10px 20px;border:none;border-radius:8px;background:#3b82f6;
                  color:#fff;font-size:.9rem;font-weight:600;cursor:pointer}}
  .tm-row button:active{{background:#2563eb}}
  #tm-status{{font-size:.8rem;color:#6af;margin-top:6px;min-height:1em}}

  /* ── push button status ── */
  .btn-status{{display:flex;align-items:center;gap:10px;margin-bottom:8px;
               font-size:1.1rem;font-weight:600}}
  .btn-dot{{width:18px;height:18px;border-radius:50%;background:#555;
            flex-shrink:0;transition:background .1s}}
  .btn-dot.pressed{{background:#facc15;box-shadow:0 0 10px #ca8a04}}
  .btn-meta{{font-size:.8rem;color:#aaa}}

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
  <a href="/dataset" style="margin-left:auto;padding:5px 14px;border-radius:6px;
     background:#1e3a5f;color:#adf;font-size:.85rem;text-decoration:none;font-weight:600">
    Dataset &#x2197;
  </a>
</header>

<main class="layout">

  <!-- LEFT: camera -->
  <section class="col-left">
    <div class="card">
      <h2>Camera</h2>
      <div class="cam-wrap" id="cam-wrap">
        <img id="cam" src="/stream" alt="Camera stream">
        <button class="fullscreen-btn" onclick="toggleFullscreen()">⛶ Full Screen</button>
      </div>
    </div>
  </section>

  <!-- RIGHT: controls -->
  <section class="col-right">

    <div class="card">
      <h2>Push Button (GPIO {btn_pin})</h2>
      <div class="btn-status">
        <span class="btn-dot" id="btn-dot"></span>
        <span id="btn-label">IDLE</span>
      </div>
      <div class="btn-meta">
        Presses: <strong id="btn-count">0</strong>
        &nbsp;|&nbsp; Last: <strong id="btn-last">—</strong>
      </div>
    </div>

    <div class="card">
      <h2>Relays</h2>
      <div class="relay-grid">
        <button class="relay-btn off" id="r0" onclick="toggleRelay(0)"><span class="relay-indicator"></span><br>Relay 1<br><small>OFF</small></button>
        <button class="relay-btn off" id="r1" onclick="toggleRelay(1)"><span class="relay-indicator"></span><br>Relay 2<br><small>OFF</small></button>
        <button class="relay-btn off" id="r2" onclick="toggleRelay(2)"><span class="relay-indicator"></span><br>Relay 3<br><small>OFF</small></button>
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
  btn.innerHTML = `<span class="relay-indicator"></span><br>Relay ${{idx + 1}}<br><small>${{on ? 'ON' : 'OFF'}}</small>`;
}}

function toggleFullscreen() {{
  const wrap = document.getElementById('cam-wrap');
  if (!document.fullscreenElement) {{
    wrap.requestFullscreen().catch(() => {{}});
  }} else {{
    document.exitFullscreen();
  }}
}}

document.addEventListener('fullscreenchange', () => {{
  const btn = document.querySelector('.fullscreen-btn');
  btn.textContent = document.fullscreenElement ? '✕ Exit Full Screen' : '⛶ Full Screen';
}});

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

async function pollButton() {{
  const res = await fetch('/button');
  if (!res.ok) return;
  const d = await res.json();
  const dot = document.getElementById('btn-dot');
  dot.className = 'btn-dot ' + (d.pressed ? 'pressed' : '');
  document.getElementById('btn-label').textContent = d.pressed ? 'PRESSED' : 'IDLE';
  document.getElementById('btn-count').textContent = d.count;
  document.getElementById('btn-last').textContent  = d.last_ts;
}}
setInterval(pollButton, 150);
pollButton();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return _HTML.format(ssid=_ssid, ip=_ip, btn_pin=_config["button_pin"])


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


@app.route("/button")
def button():
    return jsonify(_button_state)


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


@app.route("/dataset")
def dataset_page():
    return _DATASET_HTML


@app.route("/dataset/list")
def dataset_list():
    files = sorted(
        (p.name for p in DATASET_DIR.glob("*.jpg")),
        reverse=True,
    )
    return jsonify(files)


@app.route("/dataset/img/<filename>")
def dataset_img(filename: str):
    path = DATASET_DIR / filename
    if not path.exists() or not path.is_file():
        return Response("Not found", status=404)
    return send_file(str(path.resolve()), mimetype="image/jpeg")


@app.route("/dataset/delete", methods=["POST"])
def dataset_delete():
    data = request.get_json(force=True)
    names = data.get("files", [])
    deleted = []
    for name in names:
        path = DATASET_DIR / name
        # Safety: only delete .jpg files inside DATASET_DIR
        if path.parent.resolve() == DATASET_DIR.resolve() and path.suffix == ".jpg" and path.exists():
            path.unlink()
            deleted.append(name)
    print(f"[DATASET] Deleted {len(deleted)} file(s)")
    return jsonify(deleted=deleted)


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
    threading.Thread(target=_button_monitor, daemon=True).start()
    print(f"[BTN] Monitoring GPIO {_config['button_pin']} (pull-up, active LOW)")
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
