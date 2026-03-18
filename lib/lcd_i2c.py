"""
HD44780 LCD driver over PCF8574 I2C backpack.
Uses smbus2 for I2C communication.

Standard PCF8574 wiring:
  bit7 (0x80) → D7   bit6 (0x40) → D6
  bit5 (0x20) → D5   bit4 (0x10) → D4
  bit3 (0x08) → BL   bit2 (0x04) → E
  bit1 (0x02) → RW   bit0 (0x01) → RS
"""

import time
import smbus2

# PCF8574 bit masks
_BL  = 0x08  # Backlight
_EN  = 0x04  # Enable (latch on falling edge)
_RW  = 0x02  # Read/Write — always 0 (write)
_RS  = 0x01  # Register Select: 0=cmd, 1=data

# HD44780 commands
_LCD_CLEAR       = 0x01
_LCD_HOME        = 0x02
_LCD_ENTRY_MODE  = 0x06  # Increment cursor, no display shift
_LCD_DISPLAY_ON  = 0x0C  # Display on, cursor off, blink off
_LCD_FUNCTION_4B = 0x28  # 4-bit, 2-line, 5×8 font
_LCD_SET_DDRAM   = 0x80  # OR with DDRAM address

class LcdI2C:
    """HD44780 LCD driver via PCF8574 I2C backpack."""

    def __init__(self, bus: int = 1, address: int = 0x27,
                 rows: int = 4, cols: int = 16):
        self._addr = address
        self._rows = rows
        self._cols = cols
        self._row_offsets = [0x00, 0x40, cols, 0x40 + cols]
        self._bl = _BL  # backlight on by default
        self._bus = smbus2.SMBus(bus)
        self.init()

    # ------------------------------------------------------------------ #
    # Low-level I2C / pulse                                                #
    # ------------------------------------------------------------------ #

    def _write_i2c(self, data: int):
        self._bus.write_byte(self._addr, data)

    def _pulse_enable(self, data: int):
        self._write_i2c(data | _EN)
        time.sleep(0.0005)
        self._write_i2c(data & ~_EN)
        time.sleep(0.0005)

    def _write_4bits(self, nibble: int):
        """Send upper 4 bits of nibble via E pulse."""
        self._pulse_enable((nibble & 0xF0) | self._bl)

    # ------------------------------------------------------------------ #
    # Mid-level send / command / char                                       #
    # ------------------------------------------------------------------ #

    def _send(self, value: int, mode: int):
        """Send a full byte as two 4-bit nibbles. mode = 0 (cmd) or _RS (data)."""
        high = (value & 0xF0) | self._bl | mode
        low  = ((value << 4) & 0xF0) | self._bl | mode
        self._pulse_enable(high)
        self._pulse_enable(low)

    def _command(self, cmd: int):
        self._send(cmd, 0)

    def _write_char(self, char: int):
        self._send(char, _RS)

    # ------------------------------------------------------------------ #
    # Initialisation sequence (HD44780 4-bit mode)                         #
    # ------------------------------------------------------------------ #

    def init(self):
        time.sleep(0.05)  # >40 ms after power-on

        # Attempt 8-bit mode 3 times (required by HD44780 spec)
        for delay in (0.0045, 0.0045, 0.001):
            self._write_4bits(0x30)
            time.sleep(delay)

        # Switch to 4-bit mode
        self._write_4bits(0x20)
        time.sleep(0.001)

        # Function set: 4-bit, 2-line, 5×8
        self._command(_LCD_FUNCTION_4B)
        # Display on, cursor off, blink off
        self._command(_LCD_DISPLAY_ON)
        # Clear display (needs >1.6 ms)
        self._command(_LCD_CLEAR)
        time.sleep(0.002)
        # Entry mode
        self._command(_LCD_ENTRY_MODE)

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def clear(self):
        self._command(_LCD_CLEAR)
        time.sleep(0.002)

    def home(self):
        self._command(_LCD_HOME)
        time.sleep(0.002)

    def set_cursor(self, row: int, col: int):
        row = max(0, min(row, self._rows - 1))
        col = max(0, min(col, self._cols - 1))
        self._command(_LCD_SET_DDRAM | (self._row_offsets[row] + col))

    def print(self, text: str):
        for ch in text:
            self._write_char(ord(ch))

    def print_line(self, row: int, text: str):
        """Write text to a specific row, padded/truncated to LCD width."""
        self.set_cursor(row, 0)
        line = text[:self._cols].ljust(self._cols)
        self.print(line)

    def set_backlight(self, on: bool):
        self._bl = _BL if on else 0x00
        self._write_i2c(self._bl)

    def cleanup(self):
        try:
            self.clear()
            self.set_backlight(False)
        finally:
            self._bus.close()
