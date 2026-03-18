"""
PIR Motion Sensor Analyzer
PyQt6 GUI for tuning and analyzing HC-SR501 PIR sensor parameters.

Run: uv run python pir_analyzer.py
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
import RPi.GPIO as GPIO

from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QVBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QGroupBox, QHeaderView, QFrame,
)

load_dotenv()

_TZ = timezone(timedelta(hours=7))  # UTC+7 Bangkok


def _ts() -> str:
    return datetime.now(_TZ).strftime("%H:%M:%S")


# ─────────────────────────────────────────────────────────────────────────────
# GPIO worker thread
# ─────────────────────────────────────────────────────────────────────────────

class PirWorker(QThread):
    state_changed = pyqtSignal(int)          # 0 = idle, 1 = motion
    motion_event  = pyqtSignal(str, float)   # (timestamp_str, duration_secs)
    status_msg    = pyqtSignal(str)          # warmup / info messages
    finished      = pyqtSignal()

    def __init__(self, pin: int, warmup: int, poll_ms: int,
                 debounce: int, session: int):
        super().__init__()
        self.pin      = pin
        self.warmup   = warmup
        self.poll_s   = poll_ms / 1000.0
        self.debounce = debounce
        self.session  = session
        self._stop    = False

    def stop(self):
        self._stop = True

    def run(self):
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_OFF)

        try:
            # Warm-up
            self.status_msg.emit(f"Warming up {self.warmup}s…")
            deadline_warmup = time.monotonic() + self.warmup
            while time.monotonic() < deadline_warmup:
                if self._stop:
                    return
                time.sleep(0.1)

            self.status_msg.emit("Monitoring…")
            session_end = time.monotonic() + self.session
            self.state_changed.emit(0)

            while not self._stop and time.monotonic() < session_end:
                # Wait for motion start (debounced HIGH)
                consec = 0
                while not self._stop and time.monotonic() < session_end:
                    if GPIO.input(self.pin) == GPIO.HIGH:
                        consec += 1
                        if consec >= self.debounce:
                            break
                    else:
                        consec = 0
                    time.sleep(self.poll_s)

                if self._stop or time.monotonic() >= session_end:
                    break

                # Motion started
                start_ts  = _ts()
                start_mono = time.monotonic()
                self.state_changed.emit(1)

                # Wait for motion end (LOW)
                while not self._stop:
                    if GPIO.input(self.pin) == GPIO.LOW:
                        break
                    time.sleep(self.poll_s)

                duration = time.monotonic() - start_mono
                self.state_changed.emit(0)
                self.motion_event.emit(start_ts, duration)

        finally:
            GPIO.cleanup([self.pin])
            self.status_msg.emit("Stopped.")
            self.finished.emit()


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PIR Motion Sensor Analyzer")
        self.setMinimumSize(700, 550)

        self._worker: PirWorker | None = None
        self._events: list[tuple[str, float, float | None]] = []  # (ts, dur, gap)
        self._last_end_mono: float | None = None  # monotonic time when last event ended
        self._session_start: float = 0.0

        # Elapsed timer
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)

        # Title
        title = QLabel("PIR Motion Sensor Analyzer")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        root.addWidget(title)

        # Top row: params | status
        top = QHBoxLayout()
        top.addWidget(self._build_param_panel(), 1)
        top.addWidget(self._build_status_panel(), 1)
        root.addLayout(top)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)

        # Event log
        root.addWidget(self._build_log_panel(), 1)

        # Summary footer
        root.addWidget(self._build_summary_panel())

    def _build_param_panel(self) -> QGroupBox:
        box = QGroupBox("Parameters")
        form = QFormLayout(box)
        form.setSpacing(8)

        default_pin = int(os.getenv("PIR_SENSOR_PIN", 23))

        self._spin_pin = QSpinBox(); self._spin_pin.setRange(1, 40);  self._spin_pin.setValue(default_pin)
        self._spin_warmup   = QSpinBox(); self._spin_warmup.setRange(0, 30);   self._spin_warmup.setValue(5);  self._spin_warmup.setSuffix(" s")
        self._spin_poll     = QSpinBox(); self._spin_poll.setRange(10, 500);   self._spin_poll.setValue(20);   self._spin_poll.setSuffix(" ms")
        self._spin_debounce = QSpinBox(); self._spin_debounce.setRange(1, 20); self._spin_debounce.setValue(3); self._spin_debounce.setSuffix(" reads")
        self._spin_session  = QSpinBox(); self._spin_session.setRange(10, 3600); self._spin_session.setValue(60); self._spin_session.setSuffix(" s")

        form.addRow("GPIO Pin:",      self._spin_pin)
        form.addRow("Warmup:",        self._spin_warmup)
        form.addRow("Poll interval:", self._spin_poll)
        form.addRow("Debounce:",      self._spin_debounce)
        form.addRow("Session:",       self._spin_session)

        btn_row = QHBoxLayout()
        self._btn_start = QPushButton("START")
        self._btn_stop  = QPushButton("STOP")
        self._btn_stop.setEnabled(False)
        self._btn_start.clicked.connect(self._start)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_start.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 6px;")
        self._btn_stop.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 6px;")
        btn_row.addWidget(self._btn_start)
        btn_row.addWidget(self._btn_stop)
        form.addRow(btn_row)

        return box

    def _build_status_panel(self) -> QGroupBox:
        box = QGroupBox("Live Status")
        grid = QGridLayout(box)
        grid.setSpacing(8)

        self._lbl_indicator = QLabel("● IDLE")
        self._lbl_indicator.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self._lbl_indicator.setStyleSheet("color: gray;")

        self._lbl_elapsed    = QLabel("00:00:00")
        self._lbl_detections = QLabel("0")
        self._lbl_avg_dur    = QLabel("—")
        self._lbl_avg_gap    = QLabel("—")
        self._lbl_rate       = QLabel("—")
        self._lbl_msg        = QLabel("")
        self._lbl_msg.setStyleSheet("color: gray; font-style: italic;")

        grid.addWidget(self._lbl_indicator,    0, 0, 1, 2)
        grid.addWidget(QLabel("Elapsed:"),     1, 0); grid.addWidget(self._lbl_elapsed,    1, 1)
        grid.addWidget(QLabel("Detections:"),  2, 0); grid.addWidget(self._lbl_detections, 2, 1)
        grid.addWidget(QLabel("Avg duration:"),3, 0); grid.addWidget(self._lbl_avg_dur,    3, 1)
        grid.addWidget(QLabel("Avg gap:"),     4, 0); grid.addWidget(self._lbl_avg_gap,    4, 1)
        grid.addWidget(QLabel("Rate:"),        5, 0); grid.addWidget(self._lbl_rate,       5, 1)
        grid.addWidget(self._lbl_msg,          6, 0, 1, 2)
        grid.setRowStretch(7, 1)

        return box

    def _build_log_panel(self) -> QGroupBox:
        box = QGroupBox("Event Log")
        layout = QVBoxLayout(box)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["#", "Time (UTC+7)", "Duration", "Gap"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        btn_clear = QPushButton("Clear Log")
        btn_clear.clicked.connect(self._clear_log)
        layout.addWidget(btn_clear)

        return box

    def _build_summary_panel(self) -> QGroupBox:
        box = QGroupBox("Summary")
        row = QHBoxLayout(box)

        self._sum_total   = QLabel("Total: 0")
        self._sum_avg_dur = QLabel("Avg duration: —")
        self._sum_avg_gap = QLabel("Avg gap: —")
        self._sum_max_dur = QLabel("Max duration: —")
        self._sum_min_dur = QLabel("Min duration: —")

        for lbl in (self._sum_total, self._sum_avg_dur, self._sum_avg_gap,
                    self._sum_max_dur, self._sum_min_dur):
            lbl.setFont(QFont("Arial", 10))
            row.addWidget(lbl)

        return box

    # ── Control ───────────────────────────────────────────────────────────────

    def _start(self):
        self._events.clear()
        self._last_end_mono = None
        self._table.setRowCount(0)
        self._reset_stats()
        self._session_start = time.monotonic()
        self._timer.start()

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        for w in (self._spin_pin, self._spin_warmup, self._spin_poll,
                  self._spin_debounce, self._spin_session):
            w.setEnabled(False)

        self._worker = PirWorker(
            pin      = self._spin_pin.value(),
            warmup   = self._spin_warmup.value(),
            poll_ms  = self._spin_poll.value(),
            debounce = self._spin_debounce.value(),
            session  = self._spin_session.value(),
        )
        self._worker.state_changed.connect(self._on_state)
        self._worker.motion_event.connect(self._on_event)
        self._worker.status_msg.connect(self._lbl_msg.setText)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _stop(self):
        if self._worker:
            self._worker.stop()
        self._btn_stop.setEnabled(False)

    def _on_finished(self):
        self._timer.stop()
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        for w in (self._spin_pin, self._spin_warmup, self._spin_poll,
                  self._spin_debounce, self._spin_session):
            w.setEnabled(True)
        self._on_state(0)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_state(self, state: int):
        if state == 1:
            self._lbl_indicator.setText("● MOTION")
            self._lbl_indicator.setStyleSheet("color: #e74c3c; font-weight: bold;")
        else:
            self._lbl_indicator.setText("● IDLE")
            self._lbl_indicator.setStyleSheet("color: #27ae60; font-weight: bold;"
                                              if self._worker and self._worker.isRunning()
                                              else "color: gray;")

    def _on_event(self, ts: str, duration: float):
        end_mono = time.monotonic()
        gap: float | None = None
        if self._last_end_mono is not None:
            gap = max(0.0, (end_mono - duration) - self._last_end_mono)
        self._last_end_mono = end_mono

        # Store (ts, duration, gap)
        self._events.append((ts, duration, gap))

        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self._table.setItem(row, 1, QTableWidgetItem(ts))
        self._table.setItem(row, 2, QTableWidgetItem(f"{duration:.2f}s"))
        self._table.setItem(row, 3, QTableWidgetItem(f"{gap:.2f}s" if gap is not None else "—"))
        self._table.scrollToBottom()

        self._update_stats()

    def _tick(self):
        elapsed = int(time.monotonic() - self._session_start)
        h, rem = divmod(elapsed, 3600)
        m, s   = divmod(rem, 60)
        self._lbl_elapsed.setText(f"{h:02d}:{m:02d}:{s:02d}")

    # ── Stats ─────────────────────────────────────────────────────────────────

    def _update_stats(self):
        n = len(self._events)
        durations = [e[1] for e in self._events]
        gaps      = [e[2] for e in self._events if e[2] is not None]

        avg_dur = sum(durations) / n if n else None
        avg_gap = sum(gaps) / len(gaps) if gaps else None
        max_dur = max(durations) if durations else None
        min_dur = min(durations) if durations else None

        elapsed_min = (time.monotonic() - self._session_start) / 60.0
        rate = n / elapsed_min if elapsed_min > 0 else 0.0

        self._lbl_detections.setText(str(n))
        self._lbl_avg_dur.setText(f"{avg_dur:.2f}s" if avg_dur is not None else "—")
        self._lbl_avg_gap.setText(f"{avg_gap:.2f}s" if avg_gap is not None else "—")
        self._lbl_rate.setText(f"{rate:.1f}/min")

        self._sum_total.setText(f"Total: {n}")
        self._sum_avg_dur.setText(f"Avg duration: {avg_dur:.2f}s" if avg_dur is not None else "Avg duration: —")
        self._sum_avg_gap.setText(f"Avg gap: {avg_gap:.2f}s" if avg_gap is not None else "Avg gap: —")
        self._sum_max_dur.setText(f"Max duration: {max_dur:.2f}s" if max_dur is not None else "Max duration: —")
        self._sum_min_dur.setText(f"Min duration: {min_dur:.2f}s" if min_dur is not None else "Min duration: —")

    def _reset_stats(self):
        self._lbl_elapsed.setText("00:00:00")
        self._lbl_detections.setText("0")
        self._lbl_avg_dur.setText("—")
        self._lbl_avg_gap.setText("—")
        self._lbl_rate.setText("—")
        self._sum_total.setText("Total: 0")
        self._sum_avg_dur.setText("Avg duration: —")
        self._sum_avg_gap.setText("Avg gap: —")
        self._sum_max_dur.setText("Max duration: —")
        self._sum_min_dur.setText("Min duration: —")

    def _clear_log(self):
        self._events.clear()
        self._last_end_mono = None
        self._table.setRowCount(0)
        self._reset_stats()

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
