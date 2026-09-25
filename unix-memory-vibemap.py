#!/usr/bin/env python3
"""
Unix Memory Vibemap - a live, self-refreshing treemap of process memory usage.

On Linux, reads /proc for per-process RSS memory. On FreeBSD (which has
no /proc by default), shells out to `ps` instead, which reports the same
RSS figure via a portable keyword format. Both paths aggregate
multi-process applications (Firefox, Chrome, etc.) into single blocks,
and render the result as a squarified treemap that redraws itself on a
timer.

Tested targets:
  1. FreeBSD (via `ps`)
  2. Linux distributions where a GUI is welcome, e.g. Kubuntu / other
     desktop-oriented distros (via /proc, unchanged from earlier versions)
"""

import platform
import subprocess
import sys
import re
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton
)
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QShortcut, QKeySequence
)


IS_FREEBSD = platform.system() == "FreeBSD"
IS_LINUX = platform.system() == "Linux"


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

# Same grouping rules used in generate-memory-data.sh, ported to Python.
APP_GROUPS = [
    (re.compile(r"^firefox"), "firefox"),
    (re.compile(r"^(web content|webextensions|isolated web co|"
                r"privileged content|rdd|socket process|utility process|"
                r"gmp-|gpu process)", re.IGNORECASE), "firefox"),
    (re.compile(r"^(chrome|chromium|google-chrome)", re.IGNORECASE), "chrome"),
    (re.compile(r"^thunderbird", re.IGNORECASE), "thunderbird"),
    (re.compile(r"^(code|vscode)", re.IGNORECASE), "code"),
    (re.compile(r"^java", re.IGNORECASE), "java"),
    (re.compile(r"^python", re.IGNORECASE), "python"),
    (re.compile(r"^node", re.IGNORECASE), "node"),
    (re.compile(r"^vlc", re.IGNORECASE), "vlc"),
]

MIN_MEMORY_MB = 1.0  # ignore anything smaller than this


def format_memory(mb: float) -> str:
    """Format a memory value in MB, switching to GB at 1 GB (1024 MB)
    and above for easier reading."""
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"


def group_name(cmd: str) -> str:
    for pattern, name in APP_GROUPS:
        if pattern.match(cmd):
            return name
    return cmd


def read_process_memory() -> dict[str, float]:
    """
    Gather per-process resident memory (RSS), aggregated by grouped
    application name. Returns {name: memory_mb}.

    Dispatches to a platform-specific backend: /proc on Linux, `ps` on
    FreeBSD. Both apply the same grouping and minimum-size filtering so
    behaviour is identical regardless of source.
    """
    if IS_FREEBSD:
        totals = _read_process_memory_freebsd()
    else:
        # Default to /proc, which also covers other Linux-like systems
        # where a GUI treemap (rather than a terminal tool) is likely
        # to be wanted.
        totals = _read_process_memory_linux()

    # Apply minimum-size filter after aggregation, so grouped apps
    # (e.g. many small firefox helper processes) still count toward
    # the combined total even if individual helpers are tiny.
    return {name: mb for name, mb in totals.items() if mb > MIN_MEMORY_MB}


def _read_process_memory_linux() -> dict[str, float]:
    """
    Walk /proc/[pid]/status for every process, summing VmRSS values (kB)
    by grouped application name.
    """
    import os

    totals: dict[str, float] = {}

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid_dir = f"/proc/{entry}"

        try:
            with open(f"{pid_dir}/comm", "r") as f:
                cmd = f.read().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue

        if not cmd:
            continue

        try:
            with open(f"{pid_dir}/status", "r") as f:
                status_text = f.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue

        match = re.search(r"^VmRSS:\s+(\d+)\s+kB", status_text, re.MULTILINE)
        if not match:
            continue

        rss_kb = int(match.group(1))
        rss_mb = rss_kb / 1024.0

        # Guard against implausible readings (e.g. a transient /proc
        # race condition returning garbage) so one bad sample can't
        # dominate the whole treemap.
        if rss_mb <= 0 or rss_mb > 64_000:
            continue

        app = group_name(cmd)
        totals[app] = totals.get(app, 0.0) + rss_mb

    return totals


def _read_process_memory_freebsd() -> dict[str, float]:
    """
    FreeBSD has no /proc by default, so shell out to `ps` instead.

    IMPORTANT: FreeBSD's `ps -o comm=,rss=` does NOT produce two columns.
    Because `-o` is the BSD-style (dashed) option, FreeBSD's ps treats
    everything after the first keyword's `=` as that keyword's literal
    header-text override -- so `comm=,rss=` means "keyword comm, with
    the (normally suppressed) header text `,rss=`", not "comm and rss
    as two keywords". Confirmed on FreeBSD 15.1-RELEASE: this produced
    a single unlabeled comm-only column with a stray ",rss=" leaking
    out as literal first-line text, and no RSS figures at all.

    Two separate `-o` options avoid the ambiguity entirely, since each
    one then only ever contains a single keyword. RSS is requested
    first (fixed-width, numeric, never contains whitespace) and comm
    last, in case a command name ever contains a space.
    """
    totals: dict[str, float] = {}

    try:
        result = subprocess.run(
            ["ps", "-axo", "rss=", "-o", "comm="],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return totals

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue

        # RSS (a number) comes first; the rest of the line is the
        # command name. Split on the first run of whitespace only,
        # so a command name containing spaces survives intact.
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue

        rss_str, cmd = parts
        cmd = cmd.strip()

        # FreeBSD's `comm` is often a full path for ports-installed
        # software; keep only the basename for consistent grouping.
        cmd = cmd.rsplit("/", 1)[-1]

        if not cmd or not rss_str.isdigit():
            continue

        rss_kb = int(rss_str)
        rss_mb = rss_kb / 1024.0

        if rss_mb <= 0 or rss_mb > 64_000:
            continue

        app = group_name(cmd)
        totals[app] = totals.get(app, 0.0) + rss_mb

    return totals


# ---------------------------------------------------------------------------
# Squarified treemap layout (Bruls, Huizing, van Wijk algorithm)
# ---------------------------------------------------------------------------

@dataclass
class TreemapNode:
    name: str
    value: float
    rect: QRectF


def squarify(data: list[tuple[str, float]], x: float, y: float,
             w: float, h: float) -> list[TreemapNode]:
    """
    Lay out (name, value) pairs into a squarified treemap within the
    rectangle (x, y, w, h). `value` in each returned TreemapNode is the
    original, real value passed in (e.g. MB) -- NOT the internal
    area-scaled number used for the layout math.
    """
    items = sorted(data, key=lambda t: t[1], reverse=True)
    total = sum(v for _, v in items)
    if total <= 0 or not items:
        return []

    # Scale values to the rectangle's area so the layout math works in
    # area units rather than raw MB. This scaled number is used only
    # for geometry -- the real value is carried alongside it and is
    # what gets attached to each TreemapNode and shown on screen.
    area_total = w * h
    scaled = [(name, v, v / total * area_total) for name, v in items]

    nodes: list[TreemapNode] = []
    _squarify_recursive(scaled, x, y, w, h, nodes)
    return nodes


def _worst_ratio(row: list[float], length: float) -> float:
    """
    Worst (largest) aspect ratio produced by laying `row` out as a strip
    of total length `length`, per Bruls, Huizing & van Wijk (2000).
    Uses row sum, and min/max of the row -- NOT a per-item loop -- which
    is what keeps this monotonic and lets rows correctly stop growing.
    """
    if not row:
        return float("inf")
    row_sum = sum(row)
    if row_sum <= 0 or length <= 0:
        return float("inf")
    row_max = max(row)
    row_min = min(row)
    s2 = row_sum * row_sum
    l2 = length * length
    return max((l2 * row_max) / s2, s2 / (l2 * row_min))


def _squarify_recursive(items: list[tuple[str, float, float]], x: float,
                         y: float, w: float, h: float,
                         out: list[TreemapNode]) -> None:
    """
    Each item in `items` is (name, real_value, area_value). Geometry is
    computed from area_value; real_value is passed through unchanged
    into the output nodes.
    """
    if not items or w <= 0 or h <= 0:
        return

    # Always lay the current row along the shorter side, so the row
    # itself is a thin strip and remaining items get the (still large)
    # rest of the rectangle. This is what makes rows alternate between
    # horizontal and vertical as the recursion proceeds.
    horizontal = w < h  # row fills full width, stacks going down
    length = w if horizontal else h

    remaining = list(items)
    row: list[float] = [remaining[0][2]]
    remaining = remaining[1:]

    while remaining:
        candidate = row + [remaining[0][2]]
        if _worst_ratio(candidate, length) <= _worst_ratio(row, length):
            row = candidate
            remaining = remaining[1:]
        else:
            break

    row_items = items[: len(row)]
    remaining_items = items[len(row):]

    row_area = sum(row)
    row_thickness = row_area / length if length > 0 else 0

    offset = 0.0
    for (name, real_value, _area_value), area_len in zip(row_items, row):
        item_len = area_len / row_thickness if row_thickness > 0 else 0
        if horizontal:
            # Row is a horizontal strip at the top, items laid left-to-right
            rect = QRectF(x + offset, y, item_len, row_thickness)
        else:
            # Row is a vertical strip at the left, items laid top-to-bottom
            rect = QRectF(x, y + offset, row_thickness, item_len)
        out.append(TreemapNode(name=name, value=real_value, rect=rect))
        offset += item_len

    if horizontal:
        _squarify_recursive(remaining_items, x, y + row_thickness,
                             w, h - row_thickness, out)
    else:
        _squarify_recursive(remaining_items, x + row_thickness, y,
                             w - row_thickness, h, out)


# ---------------------------------------------------------------------------
# Rendering widget
# ---------------------------------------------------------------------------

# A fixed, pleasant palette (Tableau10-like), assigned consistently
# by name so a given app keeps its color across refreshes.
PALETTE = [
    "#4C78A8", "#F58518", "#E45756", "#72B7B2", "#54A24B",
    "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC",
]


class TreemapWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.nodes: list[TreemapNode] = []
        self.total_mb: float = 0.0
        self.color_map: dict[str, QColor] = {}
        self._last_totals: dict[str, float] = {}
        self.setMinimumSize(640, 480)

    def set_data(self, totals: dict[str, float]) -> None:
        self._last_totals = dict(totals)
        self.total_mb = sum(totals.values())
        rect_w, rect_h = self.width(), self.height()
        self.nodes = squarify(list(totals.items()), 0, 0, rect_w, rect_h)
        self._assign_colors(totals.keys())
        self.update()

    def _assign_colors(self, names) -> None:
        for name in names:
            if name not in self.color_map:
                idx = len(self.color_map) % len(PALETTE)
                self.color_map[name] = QColor(PALETTE[idx])

    def resizeEvent(self, event) -> None:
        # Re-layout on resize using the last known real values, not the
        # nodes' geometry, so no rounding or scaling can drift in.
        if self._last_totals:
            self.nodes = squarify(
                list(self._last_totals.items()), 0, 0,
                self.width(), self.height()
            )
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#0A0A0A"))

        if not self.nodes:
            painter.setPen(QColor("#888888"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No process data")
            painter.end()
            return

        label_font = QFont("Sans", 10)
        small_font = QFont("Sans", 8)

        for node in self.nodes:
            color = self.color_map.get(node.name, QColor("#666666"))
            painter.fillRect(node.rect, color)
            painter.setPen(QColor("#0A0A0A"))
            painter.drawRect(node.rect)

            # Only draw text if the rectangle is large enough.
            rw, rh = node.rect.width(), node.rect.height()
            if rw > 30 and rh > 16:
                painter.setPen(QColor("#FFFFFF"))
                painter.setFont(label_font)
                fm = QFontMetrics(label_font)
                name_text = node.name
                if fm.horizontalAdvance(name_text) > rw - 8:
                    name_text = fm.elidedText(
                        name_text, Qt.ElideRight, int(rw - 8)
                    )
                painter.drawText(
                    QRectF(node.rect.x() + 4, node.rect.y() + 2, rw - 8, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    name_text,
                )

                if rh > 32:
                    painter.setFont(small_font)
                    painter.setPen(QColor("#DDDDDD"))
                    mem_text = format_memory(node.value)
                    painter.drawText(
                        QRectF(
                            node.rect.x() + 4,
                            node.rect.y() + rh - 18,
                            rw - 8,
                            16,
                        ),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        mem_text,
                    )

        painter.end()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Unix Memory Vibemap")
        self.resize(960, 780)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # --- Header / stats row ---
        self.stats_label = QLabel("Loading…")
        self.stats_label.setStyleSheet(
            "color: #E0E0E0; font-size: 13px; padding: 8px; "
            "background: #1A1A1A; border: 1px solid #333; border-radius: 4px;"
        )
        layout.addWidget(self.stats_label)

        # --- Controls row ---
        controls = QHBoxLayout()

        interval_label = QLabel("Refresh interval:")
        interval_label.setStyleSheet("color: #E0E0E0;")
        controls.addWidget(interval_label)

        self.interval_box = QComboBox()
        self.interval_box.addItem("5 seconds", 5000)
        self.interval_box.addItem("15 seconds", 15000)
        self.interval_box.addItem("30 seconds", 30000)
        self.interval_box.addItem("60 seconds", 60000)
        self.interval_box.setCurrentIndex(1)  # default 15s
        self.interval_box.currentIndexChanged.connect(self._on_interval_changed)
        self.interval_box.setStyleSheet(self._combo_box_style())
        controls.addWidget(self.interval_box)

        self.refresh_btn = QPushButton("Refresh now")
        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh_btn.setStyleSheet(self._button_style())
        controls.addWidget(self.refresh_btn)

        controls.addStretch()
        layout.addLayout(controls)

        # --- Treemap canvas, resizes to fill available space ---
        self.treemap = TreemapWidget()
        layout.addWidget(self.treemap, stretch=1)

        self.setStyleSheet("background: #0F0F0F;")

        # Ctrl+Q quits directly via Qt's own shortcut handling, rather
        # than relying on the window manager to translate a key combo
        # into a close/quit request. This matters on FreeBSD guests in
        # VirtualBox, where combos like Alt+F4 can fail to reach the
        # window manager at all -- Ctrl+Q here is handled entirely
        # inside the app, bypassing that path.
        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(QApplication.instance().quit)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(15000)

        self.refresh()

    @staticmethod
    def _combo_box_style() -> str:
        # Explicit colors for every part of the combo box, including the
        # popup list -- without this, some Plasma/Breeze theme combinations
        # render the closed box's text invisible and show only one item
        # at a time in the dropdown.
        return """
            QComboBox {
                color: #E0E0E0;
                background-color: #1A1A1A;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 22px;
            }
            QComboBox:hover {
                border: 1px solid #666;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                color: #E0E0E0;
                background-color: #1A1A1A;
                border: 1px solid #444;
                selection-background-color: #3A6EA5;
                selection-color: #FFFFFF;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                min-height: 24px;
                padding: 2px 8px;
            }
        """

    @staticmethod
    def _button_style() -> str:
        return """
            QPushButton {
                color: #E0E0E0;
                background-color: #1A1A1A;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background-color: #252525;
                border: 1px solid #666;
            }
            QPushButton:pressed {
                background-color: #2E2E2E;
            }
        """

    def _on_interval_changed(self) -> None:
        ms = self.interval_box.currentData()
        self.timer.start(ms)

    def refresh(self) -> None:
        totals = read_process_memory()
        self.treemap.set_data(totals)

        total_mb = sum(totals.values())
        count = len(totals)
        if totals:
            top_name = max(totals, key=totals.get)
            top_mb = totals[top_name]
            top_text = f"{top_name} ({format_memory(top_mb)})"
        else:
            top_text = "—"

        self.stats_label.setText(
            f"Total Memory: {format_memory(total_mb)}   |   "
            f"Processes: {count}   |   "
            f"Top Process: {top_text}"
        )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
