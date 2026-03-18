# Raspberry Pi 4B Hardware Test Suite

Interactive CLI to test all GPIO-connected hardware on a Raspberry Pi 4B.

---

## Hardware

| Component | GPIO Pins |
|-----------|-----------|
| Relay 1 | GPIO 20 |
| Relay 2 | GPIO 21 |
| Relay 3 | GPIO 12 |
| PIR Sensor | GPIO 23 |
| TM1637 H0 | CLK 4 / DIO 17 |
| TM1637 H1 | CLK 27 / DIO 22 |
| TM1637 H2 | CLK 5 / DIO 6 |
| TM1637 H3 | CLK 13 / DIO 19 |
| TM1637 H4 | CLK 26 / DIO 16 |
| LCD 16×4 | I2C bus 1, address 0x27 |
| Camera | CSI |

---

## Project Structure

```
raspberry-pi-tester/
├── pyproject.toml        # uv project & dependencies
├── .env                  # pin / I2C configuration
├── main.py               # interactive test menu
├── lib/
│   ├── tm1637.py         # TM1637 bit-bang driver
│   └── lcd_i2c.py        # HD44780 over PCF8574 I2C
└── tests/
    ├── test_relay.py
    ├── test_pir.py
    ├── test_tm1637.py
    ├── test_lcd.py
    └── test_camera.py
```

---

## Setup

### 1. Enable I2C and Camera

```bash
sudo raspi-config
# Interface Options → I2C → Enable
# Interface Options → Legacy Camera → Enable  (older modules)
sudo reboot
```

### 2. Install system packages

`picamera2` requires `libcamera` native bindings that are only available via apt:

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-rpi.gpio python3-smbus2
```

### 3. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # or re-login
```

### 4. Copy project to the Pi

```bash
# From your dev machine:
scp -r . pi@raspberrypi.local:~/raspberry-pi-tester/

# On the Pi:
cd ~/raspberry-pi-tester
```

### 5. Create virtual environment

```bash
uv venv --system-site-packages
```

`--system-site-packages` exposes the apt-installed `picamera2` / `libcamera`
packages inside the venv.

### 6. Install dependencies

```bash
uv sync
```

### 7. Verify

```bash
uv run python -c "import RPi.GPIO; import smbus2; import picamera2; print('OK')"
```

---

## Run

```bash
uv run python main.py
```

```
==========================================
  Raspberry Pi 4B  Hardware Test Suite
==========================================
  1. Test Relays   (GPIO 20, 21, 12)
  2. Test PIR      (GPIO 23)
  3. Test TM1637   (H0-H4, 5 displays)
  4. Test LCD 16x4 (I2C 0x27)
  5. Test Camera   (picamera2)
  --------------------------------------
  6. Run ALL tests sequentially
  --------------------------------------
  0. Exit
==========================================
```

---

## Tests

### 1. Relays
Each relay energises for 1 s then de-energises. Confirm each click audibly.

### 2. PIR Sensor
Uses relays and the LCD as visual indicators:

| Relay | Meaning |
|-------|---------|
| RELAY1 | ON the entire time the test is running |
| RELAY2 | ON while motion is detected |
| RELAY3 | ON briefly after motion stops |

The LCD shows a rolling 3-line log of events (timestamp + state).
Test auto-passes after 3 motion detections; fails on 30 s timeout.

### 3. TM1637 Displays
Each of the 5 displays cycles: `8888` → `1234` → `HELO` → `0000` → `12:34` → blank.
Confirm each display visually.

### 4. LCD 16×4
Fills all 4 rows, tests cursor positioning, and toggles the backlight.
Confirm visually.

### 5. Camera
Captures `/tmp/camera_test.jpg` and checks file size > 10 KB.
Confirm image quality.

---

## Configuration

All pins and addresses live in `.env` — no code changes needed when rewiring:

```ini
RELAY_1=20
RELAY_2=21
RELAY_3=12
PIR_SENSOR_PIN=23
TM1637_H0_CLK=4
TM1637_H0_DIO=17
TM1637_H1_CLK=27
TM1637_H1_DIO=22
TM1637_H2_CLK=5
TM1637_H2_DIO=6
TM1637_H3_CLK=13
TM1637_H3_DIO=19
TM1637_H4_CLK=26
TM1637_H4_DIO=16
LCD_I2C_ADDRESS=0x27
LCD_I2C_BUS=1
LCD_COLS=20
LCD_ROWS=4
```

Find the LCD I2C address:

```bash
sudo i2cdetect -y 1
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ImportError: RPi.GPIO` | `sudo apt install -y python3-rpi.gpio` |
| `ImportError: picamera2` | `sudo apt install -y python3-picamera2` |
| LCD shows nothing | Run `i2cdetect -y 1`; update `LCD_I2C_ADDRESS` in `.env` |
| LCD shows blocks only | Adjust the contrast pot on the I2C backpack |
| Camera `RuntimeError` | Enable camera in `raspi-config`; reseat ribbon cable |
| TM1637 shows nothing | Check CLK/DIO wiring; verify 3.3 V on VCC |
| PIR never triggers | Allow 30–60 s warm-up; check 5 V on VCC |
