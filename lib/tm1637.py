"""
TM1637 4-digit 7-segment display driver.
Uses bit-bang GPIO (CLK + DIO) — no hardware I2C required.
"""

import time
import RPi.GPIO as GPIO

# Commands
_CMD_DATA   = 0x40  # Write data, auto-increment address
_CMD_ADDR   = 0xC0  # Set starting address (0xC0–0xC3)
_CMD_DISP   = 0x80  # Display control (| brightness[0:2] | on[3])

# 7-segment encoding: index = character
# 0-9, A, b, C, d, E, F, space, dash
_SEGMENTS = [
    0x3F,  # 0
    0x06,  # 1
    0x5B,  # 2
    0x4F,  # 3
    0x66,  # 4
    0x6D,  # 5
    0x7D,  # 6
    0x07,  # 7
    0x7F,  # 8
    0x6F,  # 9
    0x77,  # A
    0x7C,  # b
    0x39,  # C
    0x5E,  # d
    0x79,  # E
    0x71,  # F
]

_CHAR_MAP = {str(i): _SEGMENTS[i] for i in range(10)}
_CHAR_MAP.update({
    'A': 0x77, 'a': 0x77,
    'B': 0x7C, 'b': 0x7C,
    'C': 0x39, 'c': 0x39,
    'D': 0x5E, 'd': 0x5E,
    'E': 0x79, 'e': 0x79,
    'F': 0x71, 'f': 0x71,
    'G': 0x3D, 'g': 0x3D,
    'H': 0x76, 'h': 0x76,
    'I': 0x06, 'i': 0x06,
    'J': 0x1E, 'j': 0x1E,
    'L': 0x38, 'l': 0x38,
    'N': 0x37, 'n': 0x37,
    'O': 0x3F, 'o': 0x5C,
    'P': 0x73, 'p': 0x73,
    'R': 0x50, 'r': 0x50,
    'S': 0x6D, 's': 0x6D,
    'T': 0x78, 't': 0x78,
    'U': 0x3E, 'u': 0x1C,
    'Y': 0x6E, 'y': 0x6E,
    ' ': 0x00,
    '-': 0x40,
    '_': 0x08,
})


class TM1637:
    """Driver for TM1637 4-digit 7-segment display."""

    def __init__(self, clk_pin: int, dio_pin: int, brightness: int = 3):
        self._clk = clk_pin
        self._dio = dio_pin
        self._brightness = max(0, min(7, brightness))
        self._on = True

        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)

        GPIO.setup(self._clk, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self._dio, GPIO.OUT, initial=GPIO.LOW)

    # ------------------------------------------------------------------ #
    # Low-level protocol                                                    #
    # ------------------------------------------------------------------ #

    def _bit_delay(self):
        time.sleep(0.000001)  # 1 µs

    def _start(self):
        GPIO.output(self._dio, GPIO.HIGH)
        GPIO.output(self._clk, GPIO.HIGH)
        self._bit_delay()
        GPIO.output(self._dio, GPIO.LOW)
        self._bit_delay()

    def _stop(self):
        GPIO.output(self._clk, GPIO.LOW)
        self._bit_delay()
        GPIO.output(self._dio, GPIO.LOW)
        self._bit_delay()
        GPIO.output(self._clk, GPIO.HIGH)
        self._bit_delay()
        GPIO.output(self._dio, GPIO.HIGH)
        self._bit_delay()

    def _write_byte(self, byte: int) -> bool:
        """Send one byte LSB-first. Returns True if ACK received."""
        for _ in range(8):
            GPIO.output(self._clk, GPIO.LOW)
            self._bit_delay()
            GPIO.output(self._dio, GPIO.HIGH if (byte & 0x01) else GPIO.LOW)
            self._bit_delay()
            GPIO.output(self._clk, GPIO.HIGH)
            self._bit_delay()
            byte >>= 1

        # Read ACK: release DIO, clock one pulse, read
        GPIO.output(self._clk, GPIO.LOW)
        GPIO.setup(self._dio, GPIO.IN)
        self._bit_delay()
        GPIO.output(self._clk, GPIO.HIGH)
        self._bit_delay()
        ack = GPIO.input(self._dio) == GPIO.LOW
        GPIO.output(self._clk, GPIO.LOW)
        self._bit_delay()
        GPIO.setup(self._dio, GPIO.OUT, initial=GPIO.LOW)
        return ack

    # ------------------------------------------------------------------ #
    # Mid-level commands                                                    #
    # ------------------------------------------------------------------ #

    def _write_data_cmd(self):
        self._start()
        self._write_byte(_CMD_DATA)
        self._stop()

    def _write_dsp_ctrl(self):
        ctrl = _CMD_DISP | (self._brightness & 0x07)
        if self._on:
            ctrl |= 0x08
        self._start()
        self._write_byte(ctrl)
        self._stop()

    def _write_segments(self, segments: list[int], pos: int = 0):
        self._write_data_cmd()
        self._start()
        self._write_byte(_CMD_ADDR | (pos & 0x03))
        for seg in segments:
            self._write_byte(seg & 0xFF)
        self._stop()
        self._write_dsp_ctrl()

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def set_brightness(self, brightness: int, on: bool = True):
        self._brightness = max(0, min(7, brightness))
        self._on = on
        self._write_dsp_ctrl()

    def clear(self):
        self._write_segments([0x00, 0x00, 0x00, 0x00])

    def show_number(self, num: int, leading_zeros: bool = False, colon: bool = False):
        """Display an integer 0–9999. Negative numbers show '----'."""
        if num < 0 or num > 9999:
            segs = [0x40, 0x40, 0x40, 0x40]  # ----
        else:
            digits = [
                (num // 1000) % 10,
                (num // 100)  % 10,
                (num // 10)   % 10,
                num           % 10,
            ]
            segs = [_SEGMENTS[d] for d in digits]
            if not leading_zeros:
                # Blank leading zeros (but always show last digit)
                for i in range(3):
                    if digits[i] == 0 and all(d == 0 for d in digits[:i+1]):
                        segs[i] = 0x00

        if colon:
            segs[1] |= 0x80

        self._write_segments(segs)

    def show_number_hex(self, num: int):
        """Display a 16-bit value as 4 hex digits."""
        num = num & 0xFFFF
        segs = [
            _SEGMENTS[(num >> 12) & 0xF],
            _SEGMENTS[(num >> 8)  & 0xF],
            _SEGMENTS[(num >> 4)  & 0xF],
            _SEGMENTS[ num        & 0xF],
        ]
        self._write_segments(segs)

    def show_string(self, s: str):
        """Display up to 4 characters. Unsupported chars shown as blank."""
        s = s[:4].ljust(4)
        segs = [_CHAR_MAP.get(c, 0x00) for c in s]
        self._write_segments(segs)

    def cleanup(self):
        GPIO.cleanup([self._clk, self._dio])
