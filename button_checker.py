"""
button_checker.py — GPIO Push-Button Trigger Checker — Terminal CLI
Interactively configure parameters, then watch live press events.

Wiring:
    Button one leg  → GPIO pin (configurable, default 24)
    Button other leg → GND
    Internal pull-up is enabled automatically.

Run: uv run python button_checker.py
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


def _ask_choice(prompt: str, options: dict[str, str], default: str) -> str:
    """Ask user to pick from a dict of {key: label}."""
    opts_str = " / ".join(f"{k}={v}" for k, v in options.items())
    while True:
        raw = input(f"  {prompt} ({opts_str}) [{default}]: ").strip().lower()
        if raw == "":
            return default
        if raw in options:
            return raw
        print(f"    Enter one of: {', '.join(options)}")


def prompt_params() -> dict:
    default_pin = int(os.getenv("BUTTON_PIN", 24))
    print()
    print("Push-Button Trigger Checker")
    print("=" * 42)
    print("  Press Enter to accept the default value.")
    print()
    pin      = _ask("GPIO Pin          (1–40)", default_pin, 1, 40)
    debounce = _ask("Debounce       ms (5–200)",  50,  5, 200)
    session  = _ask("Session        s  (10–3600)", 60, 10, 3600)
    mode_raw = _ask_choice(
        "Pull resistor",
        {"up": "pull-up (button→GND)", "down": "pull-down (button→3.3V)"},
        "up",
    )
    pull = GPIO.PUD_UP if mode_raw == "up" else GPIO.PUD_DOWN
    # active level: pull-up → pressed=LOW, pull-down → pressed=HIGH
    active = GPIO.LOW if mode_raw == "up" else GPIO.HIGH
    return dict(pin=pin, debounce_ms=debounce, session=session,
                pull=pull, active=active, pull_label=mode_raw)


def run_session(p: dict) -> tuple[list, float]:
    """
    Run the detection loop.  Returns (events, elapsed_secs).
    events: list of (ts, duration_s, gap_s|None)
    """
    pin        = p["pin"]
    debounce_s = p["debounce_ms"] / 1000.0
    session    = p["session"]
    pull       = p["pull"]
    active     = p["active"]
    idle       = GPIO.HIGH if active == GPIO.LOW else GPIO.LOW

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(pin, GPIO.IN, pull_up_down=pull)

    events: list[tuple[str, float, float | None]] = []
    last_end_mono: float | None = None

    print()
    print(f"  GPIO pin     : {pin}")
    print(f"  Pull         : {p['pull_label']}")
    print(f"  Active level : {'LOW (button→GND)' if active == GPIO.LOW else 'HIGH (button→3.3V)'}")
    print(f"  Debounce     : {p['debounce_ms']}ms")
    print(f"  Session      : {session}s")
    print()
    print("[BTN] Monitoring — press Ctrl+C to stop early.")
    print()
    print(f"  {'#':>3}  {'Time (UTC+7)':<14} {'Hold':>9}  {'Gap':>10}")
    print(f"  {'─'*3}  {'─'*14} {'─'*9}  {'─'*10}")

    session_start = time.monotonic()
    session_end   = session_start + session

    try:
        while time.monotonic() < session_end:
            # Wait for button press (active level) with software debounce
            while time.monotonic() < session_end:
                if GPIO.input(pin) == active:
                    time.sleep(debounce_s)          # debounce wait
                    if GPIO.input(pin) == active:   # confirm still pressed
                        break
                time.sleep(0.005)

            if time.monotonic() >= session_end:
                break

            ts         = _ts()
            start_mono = time.monotonic()

            # Wait for button release (idle level)
            while GPIO.input(pin) == active:
                time.sleep(0.005)
            time.sleep(debounce_s)  # debounce on release

            end_mono = time.monotonic()
            duration = end_mono - start_mono

            gap: float | None = None
            if last_end_mono is not None:
                gap = max(0.0, start_mono - last_end_mono)
            last_end_mono = end_mono

            events.append((ts, duration, gap))
            n = len(events)
            gap_str = f"{gap:>9.2f}s" if gap is not None else f"{'—':>10}"
            print(f"  {n:>3}  {ts:<14} {duration:>8.3f}s  {gap_str}")

    finally:
        GPIO.cleanup([pin])

    elapsed = time.monotonic() - session_start
    return events, elapsed


def print_summary(events: list, elapsed: float, session: int):
    print()
    if elapsed < session:
        print("[BTN] Stopped early.")
    else:
        print(f"[BTN] Session ended ({session}s).")
    print()

    n         = len(events)
    durations = [e[1] for e in events]
    gaps      = [e[2] for e in events if e[2] is not None]

    avg_dur = sum(durations) / n        if n         else None
    max_dur = max(durations)            if durations else None
    min_dur = min(durations)            if durations else None
    avg_gap = sum(gaps) / len(gaps)     if gaps      else None
    rate    = n / (elapsed / 60.0)     if elapsed > 0 else 0.0

    w = 42
    print("═" * w)
    print(f"{'  SUMMARY':^{w}}")
    print("═" * w)
    print(f"  {'Total presses':<22}: {n}")
    print(f"  {'Elapsed':<22}: {elapsed:.1f}s")
    print(f"  {'Presses / min':<22}: {rate:.1f}")
    print()
    if avg_dur is not None:
        print(f"  {'Avg hold':<22}: {avg_dur:.3f}s")
        print(f"  {'Max hold':<22}: {max_dur:.3f}s")
        print(f"  {'Min hold':<22}: {min_dur:.3f}s")
    else:
        print("  No button presses detected.")
    if avg_gap is not None:
        print(f"  {'Avg gap between':<22}: {avg_gap:.2f}s")
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
