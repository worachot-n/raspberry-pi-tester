"""
Raspberry Pi 4B Hardware Test Suite
Run: uv run python main.py
"""

import os
import sys

from dotenv import load_dotenv
import RPi.GPIO as GPIO

from tests import test_relay, test_pir, test_tm1637, test_lcd, test_camera


def build_config() -> dict:
    load_dotenv()
    return {
        "relays": [
            int(os.getenv("RELAY_1", 20)),
            int(os.getenv("RELAY_2", 21)),
            int(os.getenv("RELAY_3", 12)),
        ],
        "pir_pin": int(os.getenv("PIR_SENSOR_PIN", 23)),
        "tm1637": [
            {"clk": int(os.getenv("TM1637_H0_CLK",  4)), "dio": int(os.getenv("TM1637_H0_DIO", 17))},
            {"clk": int(os.getenv("TM1637_H1_CLK", 27)), "dio": int(os.getenv("TM1637_H1_DIO", 22))},
            {"clk": int(os.getenv("TM1637_H2_CLK",  5)), "dio": int(os.getenv("TM1637_H2_DIO",  6))},
            {"clk": int(os.getenv("TM1637_H3_CLK", 13)), "dio": int(os.getenv("TM1637_H3_DIO", 19))},
            {"clk": int(os.getenv("TM1637_H4_CLK", 26)), "dio": int(os.getenv("TM1637_H4_DIO", 16))},
        ],
        "lcd": {
            "address": int(os.getenv("LCD_I2C_ADDRESS", "0x27"), 16),
            "bus":     int(os.getenv("LCD_I2C_BUS",  1)),
            "rows":    int(os.getenv("LCD_ROWS",      4)),
            "cols":    int(os.getenv("LCD_COLS",     20)),
        },
        "camera": {
            "width":  1920,
            "height": 1080,
        },
    }


_TESTS = [
    ("Relays   (GPIO 20, 21, 12)",    test_relay.run_test),
    ("PIR      (GPIO 23)",            test_pir.run_test),
    ("TM1637   (H0-H4, 5 displays)",  test_tm1637.run_test),
    ("LCD 16x4 (I2C 0x27)",          test_lcd.run_test),
    ("Camera   (picamera2)",          test_camera.run_test),
]

_DISPLAY_TESTS = [
    ("Relays   (GPIO 20, 21, 12)",   test_relay.run_test),
    ("TM1637   (H0-H4, 5 displays)", test_tm1637.run_test),
    ("LCD 16x4 (I2C 0x27)",         test_lcd.run_test),
]


def print_menu():
    print()
    print("=" * 42)
    print("  Raspberry Pi 4B  Hardware Test Suite")
    print("=" * 42)
    for i, (name, _) in enumerate(_TESTS, start=1):
        print(f"  {i}. Test {name}")
    print("  " + "-" * 38)
    print("  6. Run ALL tests sequentially")
    print("  7. Run Relay + TM1637 + LCD")
    print("  " + "-" * 38)
    print("  0. Exit")
    print("=" * 42)


def print_summary(results: dict):
    print()
    print("=" * 42)
    print("  TEST SUMMARY")
    print("=" * 42)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        marker = "[+]" if passed else "[!]"
        print(f"  {marker} {status}  {name}")
    print("=" * 42)
    overall = all(results.values())
    print(f"  Overall: {'ALL PASS' if overall else 'SOME TESTS FAILED'}")
    print("=" * 42)


def run_all(config: dict, tests=None):
    if tests is None:
        tests = _TESTS
    results = {}
    for name, fn in tests:
        print(f"\n{'─' * 42}")
        print(f"  Running: {name}")
        print(f"{'─' * 42}")
        try:
            results[name] = fn(config)
        except Exception as e:
            print(f"  ERROR: {e}")
            results[name] = False
    print_summary(results)


def main():
    config = build_config()

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    for pin in config["relays"]:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)   # relays OFF at startup (active-high)

    try:
        while True:
            print_menu()
            choice = input("Select option: ").strip()

            if choice == "0":
                print("Goodbye.")
                break
            elif choice in ("1", "2", "3", "4", "5"):
                idx = int(choice) - 1
                name, fn = _TESTS[idx]
                print(f"\n{'─' * 42}")
                print(f"  Running: {name}")
                print(f"{'─' * 42}")
                try:
                    passed = fn(config)
                    print(f"\n  Result: {'PASS' if passed else 'FAIL'}")
                except Exception as e:
                    print(f"\n  ERROR: {e}")
            elif choice == "6":
                run_all(config)
            elif choice == "7":
                run_all(config, _DISPLAY_TESTS)
            else:
                print("  Invalid option -- enter 0-7.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up.")


if __name__ == "__main__":
    main()
