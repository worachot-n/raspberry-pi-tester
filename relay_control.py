"""
relay_control.py — Manual Relay Controller — Terminal CLI
Manually set each relay ON or OFF interactively.

Run: uv run python relay_control.py
Exit: 0 or Ctrl+C  (all relays turned OFF on exit)
"""

import os
import sys

from dotenv import load_dotenv
import RPi.GPIO as GPIO

load_dotenv()


def build_pins() -> list[tuple[int, str]]:
    return [
        (int(os.getenv("RELAY_1", 20)), "Relay 1  (GPIO 20)"),
        (int(os.getenv("RELAY_2", 21)), "Relay 2  (GPIO 21)"),
        (int(os.getenv("RELAY_3", 12)), "Relay 3  (GPIO 12)"),
    ]


def setup(pins: list[int]):
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin in pins:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)  # all OFF at startup (active-high)


def set_relay(pin: int, on: bool):
    GPIO.output(pin, GPIO.HIGH if on else GPIO.LOW)


def all_off(pins: list[int]):
    for pin in pins:
        GPIO.output(pin, GPIO.LOW)


def print_menu(relays: list[tuple[int, str]], states: list[bool]):
    print()
    print("=" * 42)
    print("     Manual Relay Controller")
    print("=" * 42)
    for i, ((pin, label), on) in enumerate(zip(relays, states), start=1):
        state = " ON " if on else " OFF"
        print(f"  {i}. [{state}]  {label}")
    print("  " + "-" * 38)
    print("  4. All ON")
    print("  5. All OFF")
    print("  " + "-" * 38)
    print("  0. Exit (all OFF)")
    print("=" * 42)


def main():
    relays = build_pins()
    pins   = [p for p, _ in relays]
    states = [False] * len(relays)

    setup(pins)

    try:
        while True:
            print_menu(relays, states)
            choice = input("Select option: ").strip()

            if choice == "0":
                break

            elif choice in ("1", "2", "3"):
                idx  = int(choice) - 1
                pin  = pins[idx]
                _, label = relays[idx]
                action = input(f"  {label} — enter ON or OFF: ").strip().upper()
                if action == "ON":
                    set_relay(pin, True)
                    states[idx] = True
                    print(f"  → {label}: ON")
                elif action == "OFF":
                    set_relay(pin, False)
                    states[idx] = False
                    print(f"  → {label}: OFF")
                else:
                    print("  Enter ON or OFF.")

            elif choice == "4":
                for i, pin in enumerate(pins):
                    set_relay(pin, True)
                    states[i] = True
                print("  → All relays ON")

            elif choice == "5":
                all_off(pins)
                for i in range(len(states)):
                    states[i] = False
                print("  → All relays OFF")

            else:
                print("  Invalid option -- enter 0-5.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        all_off(pins)
        GPIO.cleanup(pins)
        print("All relays OFF. GPIO cleaned up.")


if __name__ == "__main__":
    main()
