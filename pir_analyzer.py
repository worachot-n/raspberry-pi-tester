"""
PIR Motion Sensor Analyzer — Terminal CLI
Interactively configure parameters, then watch live detection log.

Run: uv run python pir_analyzer.py
Stop early: Ctrl+C
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
import RPi.GPIO as GPIO

load_dotenv()

_TZ = timezone(timedelta(hours=7))  # UTC+7 Bangkok


def _ts() -> str:
    return datetime.now(_TZ).strftime("%H:%M:%S")


def _ask(prompt: str, default: int, lo: int, hi: int) -> int:
    while True:
        raw = input(f"  {prompt} [{default}]: ").strip()
        if raw == "":
            return default
        try:
            val = int(raw)
            if lo <= val <= hi:
                return val
            print(f"    Enter a value between {lo} and {hi}.")
        except ValueError:
            print("    Please enter a whole number.")


def prompt_params() -> dict:
    default_pin = int(os.getenv("PIR_SENSOR_PIN", 23))
    print()
    print("PIR Motion Sensor Analyzer")
    print("=" * 42)
    print("  Press Enter to accept the default value.")
    print()
    pin      = _ask("GPIO Pin          (1–40)", default_pin, 1, 40)
    warmup   = _ask("Warmup         s  (0–30)", 5,   0,   30)
    poll     = _ask("Poll interval  ms (10–500)", 20, 10, 500)
    debounce = _ask("Debounce reads    (1–20)", 3,   1,   20)
    session  = _ask("Session        s  (10–3600)", 60, 10, 3600)
    return dict(pin=pin, warmup=warmup, poll_ms=poll,
                debounce=debounce, session=session)


def run_session(p: dict) -> tuple[list, float]:
    """
    Run the detection loop. Returns (events, elapsed_secs).
    events: list of (ts, duration_s, gap_s|None)
    """
    pin      = p["pin"]
    poll_s   = p["poll_ms"] / 1000.0
    debounce = p["debounce"]
    session  = p["session"]

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_OFF)

    events: list[tuple[str, float, float | None]] = []
    last_end_mono: float | None = None

    print()
    print(f"  GPIO pin   : {pin}")
    print(f"  Warmup     : {p['warmup']}s")
    print(f"  Poll       : {p['poll_ms']}ms")
    print(f"  Debounce   : {debounce} reads")
    print(f"  Session    : {session}s")
    print()

    # Warm-up
    if p["warmup"] > 0:
        print(f"[PIR] Warming up {p['warmup']}s...", end="", flush=True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < p["warmup"]:
            time.sleep(0.1)
        print(" done.")

    pin_level = GPIO.input(pin)
    print(f"[PIR] Pin {pin} level after warmup: {pin_level} (0=idle, 1=motion)")
    print("[PIR] Monitoring — press Ctrl+C to stop early.")
    print()
    print(f"  {'#':>3}  {'Time (UTC+7)':<14} {'Duration':>10}  {'Gap':>10}")
    print(f"  {'─'*3}  {'─'*14} {'─'*10}  {'─'*10}")

    session_start = time.monotonic()
    session_end   = session_start + session

    try:
        while time.monotonic() < session_end:
            # Wait for debounced RISING
            consec = 0
            while time.monotonic() < session_end:
                if GPIO.input(pin) == GPIO.HIGH:
                    consec += 1
                    if consec >= debounce:
                        break
                else:
                    consec = 0
                time.sleep(poll_s)

            if time.monotonic() >= session_end:
                break

            ts         = _ts()
            start_mono = time.monotonic()

            # Wait for FALLING
            while True:
                if GPIO.input(pin) == GPIO.LOW:
                    break
                time.sleep(poll_s)

            end_mono = time.monotonic()
            duration = end_mono - start_mono

            gap: float | None = None
            if last_end_mono is not None:
                gap = max(0.0, (end_mono - duration) - last_end_mono)
            last_end_mono = end_mono

            events.append((ts, duration, gap))
            n = len(events)
            gap_str = f"{gap:>9.2f}s" if gap is not None else f"{'—':>10}"
            print(f"  {n:>3}  {ts:<14} {duration:>9.2f}s  {gap_str}")

    finally:
        GPIO.cleanup([pin])

    elapsed = time.monotonic() - session_start
    return events, elapsed


def print_summary(events: list, elapsed: float, session: int):
    print()
    if time.monotonic() - elapsed < session:
        print(f"[PIR] Stopped early.")
    else:
        print(f"[PIR] Session ended ({session}s).")
    print()

    n = len(events)
    durations = [e[1] for e in events]
    gaps      = [e[2] for e in events if e[2] is not None]

    avg_dur = sum(durations) / n       if n          else None
    max_dur = max(durations)           if durations  else None
    min_dur = min(durations)           if durations  else None
    avg_gap = sum(gaps) / len(gaps)    if gaps       else None
    rate    = n / (elapsed / 60.0)    if elapsed > 0 else 0.0

    w = 42
    print("═" * w)
    print(f"{'  SUMMARY':^{w}}")
    print("═" * w)
    print(f"  {'Total detections':<22}: {n}")
    print(f"  {'Elapsed':<22}: {elapsed:.1f}s")
    print(f"  {'Events / min':<22}: {rate:.1f}")
    print()
    if avg_dur is not None:
        print(f"  {'Avg duration':<22}: {avg_dur:.2f}s")
        print(f"  {'Max duration':<22}: {max_dur:.2f}s")
        print(f"  {'Min duration':<22}: {min_dur:.2f}s")
    else:
        print("  No motion events detected.")
    if avg_gap is not None:
        print(f"  {'Avg gap':<22}: {avg_gap:.2f}s")
    print("═" * w)
    print()


def main():
    try:
        params = prompt_params()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(0)

    events: list = []
    elapsed: float = 0.0
    try:
        events, elapsed = run_session(params)
    except KeyboardInterrupt:
        print()  # newline after ^C

    print_summary(events, elapsed, params["session"])


if __name__ == "__main__":
    main()
