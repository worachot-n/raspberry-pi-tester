"""Test: TM1637 4-digit displays (H0–H4)."""

import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.tm1637 import TM1637


def _test_one(label: str, clk: int, dio: int) -> bool:
    print(f"\n[TM1637] Testing display {label} (CLK={clk}, DIO={dio})")
    display = TM1637(clk, dio, brightness=4)

    try:
        steps = [
            (lambda: display.show_number(8888),                       "8888",  1.0),
            (lambda: display.show_number(1234),                       "1234",  1.0),
            (lambda: display.show_string("HELO"),                     "HELO",  1.0),
            (lambda: display.show_number(0, leading_zeros=True),      "0000",  1.0),
            (lambda: display.show_number(1234, colon=True),           "12:34", 1.0),
            (lambda: display.clear(),                                  "blank", 0.5),
        ]
        for fn, label_step, duration in steps:
            fn()
            print(f"  Showing: {label_step}")
            time.sleep(duration)

        answer = input(f"  Did display {label} show 8888 → 1234 → HELO → 0000 → 12:34 → blank? [y/n]: ").strip().lower()
        return answer == "y"

    except KeyboardInterrupt:
        return False
    finally:
        display.clear()
        display.cleanup()


def run_test(config: dict) -> bool:
    displays = config["tm1637"]
    results = {}

    for i, disp in enumerate(displays):
        name = f"H{i}"
        passed = _test_one(name, disp["clk"], disp["dio"])
        results[name] = passed

    print("\n[TM1637] Results:")
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False

    return all_pass
