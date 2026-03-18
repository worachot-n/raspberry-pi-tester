"""
Test: PIR motion sensor (GPIO 23) with relay indicators and LCD log.

Relay states:
  RELAY1 ON  — always ON while the test is running (system active indicator)
  RELAY2 ON  — motion detected
  RELAY3 ON  — motion stopped, held briefly then clears back to standby

LCD layout (16x4):
  Row 0: "PIR TEST"  +  detection count
  Row 1: last status line
  Row 2: second-last status line
  Row 3: third-last status line  (scrolls up on each event)
"""

import time
import sys
import os
from datetime import datetime

import RPi.GPIO as GPIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.lcd_i2c import LcdI2C

_WARMUP_SECS  = 5
_TARGET_COUNT = 3
_TIMEOUT_MS   = 30_000


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _wait_for_level(pin: int, level: int, timeout_ms: int) -> bool:
    """Poll pin until it reaches `level` or timeout. Returns True if level reached."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if GPIO.input(pin) == level:
            return True
        time.sleep(0.02)
    return False


class _State:
    """Manages relay outputs and LCD log during the PIR test."""

    def __init__(self, relay_pins: list[int], lcd: LcdI2C):
        self._r1, self._r2, self._r3 = relay_pins
        self._lcd = lcd
        self._log: list[str] = []       # rolling log, newest first
        self._count = 0

        for pin in relay_pins:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.output(self._r1, GPIO.HIGH)    # RELAY1 stays ON for the entire test

    # -- Relay helpers ----------------------------------------------------- #

    def _relays(self, r2: bool, r3: bool):
        # RELAY1 is always ON — never touched here
        GPIO.output(self._r2, GPIO.HIGH if r2 else GPIO.LOW)
        GPIO.output(self._r3, GPIO.HIGH if r3 else GPIO.LOW)

    # -- LCD helpers -------------------------------------------------------- #

    def _push_log(self, msg: str):
        """Add a line to the rolling log and refresh rows 1-3."""
        self._log.insert(0, msg[:16])
        self._log = self._log[:3]           # keep last 3 entries
        for row in range(1, 4):
            line = self._log[row - 1] if (row - 1) < len(self._log) else ""
            self._lcd.print_line(row, line)

    def _refresh_header(self):
        self._lcd.print_line(0, f"PIR det:{self._count}/{_TARGET_COUNT}")

    # -- State transitions -------------------------------------------------- #

    def standby(self):
        self._relays(r2=False, r3=False)
        msg = f"{_ts()} STANDBY"
        print(f"  [PIR] {msg}")
        self._push_log(msg)
        self._refresh_header()

    def motion_detected(self):
        self._count += 1
        self._relays(r2=True, r3=False)
        msg = f"{_ts()} MOTION #{self._count}"
        print(f"  [PIR] {msg}")
        self._push_log(msg)
        self._refresh_header()

    def motion_stopped(self):
        self._relays(r2=False, r3=True)
        msg = f"{_ts()} STOPPED"
        print(f"  [PIR] {msg}")
        self._push_log(msg)
        self._refresh_header()
        time.sleep(1.0)     # hold RELAY3 briefly so it is visible

    def done(self, passed: bool):
        self._relays(r2=False, r3=False)   # RELAY1 stays ON until cleanup
        result = "PASS" if passed else "FAIL/TIMEOUT"
        msg = f"{_ts()} {result}"
        print(f"  [PIR] {msg}")
        self._push_log(msg)
        self._refresh_header()

    def cleanup(self):
        GPIO.cleanup([self._r1, self._r2, self._r3])


def run_test(config: dict) -> bool:
    pir_pin    = config["pir_pin"]
    relay_pins = config["relays"]           # [RELAY1, RELAY2, RELAY3]
    lcd_cfg    = config["lcd"]

    print(f"\n[PIR] Setting up PIR pin {pir_pin}, relays {relay_pins}")

    # Initialise LCD
    try:
        lcd = LcdI2C(
            bus=lcd_cfg["bus"],
            address=lcd_cfg["address"],
            rows=lcd_cfg["rows"],
            cols=lcd_cfg["cols"],
        )
    except Exception as e:
        print(f"[PIR] WARNING — LCD init failed ({e}). Continuing without LCD.")
        lcd = None

    if lcd is None:
        # Fallback: minimal LCD stub so the rest of the code is unchanged
        class _NoLcd:
            def print_line(self, *_): pass
            def cleanup(self): pass
        lcd = _NoLcd()

    state = _State(relay_pins, lcd)

    try:
        GPIO.setup(pir_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

        # Warm-up
        lcd.print_line(0, "PIR warming...")
        lcd.print_line(1, f"Wait {_WARMUP_SECS}s...")
        lcd.print_line(2, "")
        lcd.print_line(3, "")
        print(f"[PIR] Warming up — wait {_WARMUP_SECS} seconds...")
        time.sleep(_WARMUP_SECS)

        state.standby()
        print(f"[PIR] Waiting for {_TARGET_COUNT} motion events (30 s timeout each)...")

        passed = False
        while state._count < _TARGET_COUNT:
            # Wait for motion ON (rising edge)
            if not _wait_for_level(pir_pin, GPIO.HIGH, _TIMEOUT_MS):
                print(f"[PIR] Timeout — {state._count}/{_TARGET_COUNT} detections.")
                break

            state.motion_detected()

            # Wait for motion OFF (falling edge) — max 10 s
            _wait_for_level(pir_pin, GPIO.LOW, 10_000)
            state.motion_stopped()

            if state._count < _TARGET_COUNT:
                state.standby()

        passed = state._count >= _TARGET_COUNT
        state.done(passed)

        if passed:
            print("[PIR] PASS — all detections received.")
        else:
            print(f"[PIR] FAIL — only {state._count}/{_TARGET_COUNT} detections.")

        return passed

    except KeyboardInterrupt:
        print("\n[PIR] Interrupted.")
        state.done(False)
        return False
    finally:
        GPIO.cleanup([pir_pin])
        state.cleanup()
        try:
            lcd.cleanup()
        except Exception:
            pass
        print("[PIR] Cleaned up.")
