"""Test: Relay outputs (GPIO 20, 21, 12)."""

import time
import RPi.GPIO as GPIO


def run_test(config: dict) -> bool:
    pins = config["relays"]
    print("\n[RELAY] Setting up pins:", pins)

    try:
        for pin in pins:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)  # HIGH = relay OFF (active-low)

        for i, pin in enumerate(pins, start=1):
            print(f"  Relay {i} (GPIO {pin}): ON  ", end="", flush=True)
            GPIO.output(pin, GPIO.LOW)   # LOW = relay ON  (active-low)
            time.sleep(1.0)
            GPIO.output(pin, GPIO.HIGH)  # HIGH = relay OFF (active-low)
            print("→ OFF")
            time.sleep(0.5)

        answer = input("\n[RELAY] Did all 3 relays click on and off? [y/n]: ").strip().lower()
        return answer == "y"

    except KeyboardInterrupt:
        print("\n[RELAY] Interrupted.")
        return False
    finally:
        GPIO.cleanup(pins)
        print("[RELAY] Pins cleaned up.")
