"""Test: LCD 16×4 via I2C (HD44780 + PCF8574 at 0x27)."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.lcd_i2c import LcdI2C


def run_test(config: dict) -> bool:
    lcd_cfg = config["lcd"]
    address = lcd_cfg["address"]
    bus = lcd_cfg["bus"]
    rows = lcd_cfg["rows"]
    cols = lcd_cfg["cols"]

    print(f"\n[LCD] Initialising LCD at I2C bus {bus}, address 0x{address:02X}")

    try:
        lcd = LcdI2C(bus=bus, address=address, rows=rows, cols=cols)
    except Exception as e:
        print(f"[LCD] FAIL — could not initialise LCD: {e}")
        return False

    try:
        # Test 1: All 4 rows
        print("[LCD] Test 1: Printing to all 4 rows...")
        lcd.print_line(0, "== LCD TEST PASS")
        lcd.print_line(1, "Row1: Hello Wrld")
        lcd.print_line(2, "Row2: 1234567890")
        lcd.print_line(3, "Row3: ABCDEFGHIJ")
        time.sleep(3)

        # Test 2: Cursor positioning
        print("[LCD] Test 2: Cursor positioning...")
        lcd.clear()
        lcd.set_cursor(0, 5)
        lcd.print("MIDDLE")
        lcd.set_cursor(3, 13)
        lcd.print("END")
        time.sleep(2)

        # Test 3: Backlight toggle
        print("[LCD] Test 3: Backlight toggle...")
        lcd.set_backlight(False)
        print("  Backlight OFF")
        time.sleep(1)
        lcd.set_backlight(True)
        print("  Backlight ON")
        time.sleep(1)

        # Done
        lcd.clear()
        lcd.print_line(0, "   TEST DONE    ")
        time.sleep(1)

        answer = (
            input(
                "\n[LCD] Did all 4 rows display correctly and backlight toggle? [y/n]: "
            )
            .strip()
            .lower()
        )
        return answer == "y"

    except KeyboardInterrupt:
        print("\n[LCD] Interrupted.")
        return False
    finally:
        try:
            lcd.cleanup()
        except Exception:
            pass
        print("[LCD] Cleaned up.")
