"""Test: PIR motion sensor (GPIO 23)."""

import time
from datetime import datetime
import RPi.GPIO as GPIO

_WARMUP_SECS    = 5
_TARGET_COUNT   = 3
_TIMEOUT_MS     = 30_000


def run_test(config: dict) -> bool:
    pin = config["pir_pin"]
    print(f"\n[PIR] Setting up pin {pin}")

    try:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

        print(f"[PIR] Warming up — please wait {_WARMUP_SECS} seconds...")
        time.sleep(_WARMUP_SECS)

        print(f"[PIR] Move in front of the sensor.")
        print(f"[PIR] Waiting for {_TARGET_COUNT} detections (30 s timeout each)...")

        count = 0
        while count < _TARGET_COUNT:
            result = GPIO.wait_for_edge(pin, GPIO.RISING, timeout=_TIMEOUT_MS)
            if result is None:
                print(f"[PIR] Timeout — only {count}/{_TARGET_COUNT} detections received.")
                return False
            count += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  Detection {count}/{_TARGET_COUNT} at {ts}")

        print("[PIR] PASS — all detections received.")
        return True

    except KeyboardInterrupt:
        print("\n[PIR] Interrupted.")
        return False
    finally:
        GPIO.cleanup([pin])
        print("[PIR] Pin cleaned up.")
