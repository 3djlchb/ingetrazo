# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The animation's resolution: the 3D viewport's own size, or a custom one
picked from the user's **presets**.

A preset is a name, a width, a height and a **lock**. Applying a locked
preset holds Width and Height read-only — a delivery size (4K for a
client) cannot be changed by a stray wheel turn — and a padlock in front
of each field says so at a glance. The presets are the user's, kept in the
settings (``QSettings``), not in the document: every project sees them."""
from __future__ import annotations

import json

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel,
                               QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from core.i18n import tr

#: The resolution modes of «Resolution:».
RES_NATIVE = "native"
RES_CUSTOM = "custom"

PRESETS_KEY = "igz_animacion/resolution_presets"

#: What a first run offers; the user's own list replaces it once edited.
DEFAULT_PRESETS = (
    {"name": "HD", "width": 1280, "height": 720, "locked": False},
    {"name": "Full HD", "width": 1920, "height": 1080, "locked": False},
    {"name": "2K QHD", "width": 2560, "height": 1440, "locked": False},
    {"name": "4K UHD", "width": 3840, "height": 2160, "locked": False},
    {"name": "Instagram 4:5", "width": 1080, "height": 1350, "locked": False},
    {"name": "1:1", "width": 1080, "height": 1080, "locked": False},
)


# ---- the presets -------------------------------------------------------------

def _clean(p) -> dict | None:
    try:
        name = str(p["name"]).strip()
        w, h = int(p["width"]), int(p["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if not name or not (16 <= w <= 16384 and 16 <= h <= 16384):
        return None
    return {"name": name, "width": w, "height": h,
            "locked": bool(p.get("locked", False))}


def load_presets(settings: QSettings | None = None) -> list:
    """The user's presets — the defaults until they are first edited."""
    st = settings or QSettings()
    raw = st.value(PRESETS_KEY, None)
    if raw is None:
        return [dict(p) for p in DEFAULT_PRESETS]
    try:
        data = json.loads(raw) if isinstance(raw, str) else list(raw)
    except (TypeError, ValueError):
        return [dict(p) for p in DEFAULT_PRESETS]
    out, seen = [], set()
    for p in data:
        p = _clean(p) if isinstance(p, dict) else None
        if p is not None and p["name"] not in seen:
            seen.add(p["name"])
            out.append(p)
    return out


def save_presets(presets, settings: QSettings | None = None) -> None:
    st = settings or QSettings()
    st.setValue(PRESETS_KEY, json.dumps(
        [p for p in (_clean(p) for p in presets) if p is not None]))


def find_preset(presets, name) -> dict | None:
    return next((p for p in presets if p["name"] == name), None)


# ---- the padlock ---------------------------------------------------------------

def lock_pixmap(locked: bool, color: QColor, size: int = 14,
                ratio: float = 1.0) -> QPixmap:
    """A padlock, closed (``locked``) or open, drawn in ``color`` — no image
    file, so it follows the theme."""
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    body = QRectF(s * 0.18, s * 0.46, s * 0.64, s * 0.46)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(body, s * 0.08, s * 0.08)
    pen = QPen(color, max(1.4, s * 0.12))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    shackle = QPainterPath()
    left, right = s * 0.32, s * 0.68
    top = s * 0.12
    if locked:
        shackle.moveTo(QPointF(left, body.top()))
        shackle.lineTo(QPointF(left, s * 0.32))
        shackle.arcTo(QRectF(left, top, right - left, (right - left)), 180, -180)
        shackle.lineTo(QPointF(right, body.top()))
    else:                                   # swung open, to the right
        shackle.moveTo(QPointF(right, body.top()))
        shackle.lineTo(QPointF(right, s * 0.30))
        shackle.arcTo(QRectF(right, top - s * 0.04, right - left,
                             (right - left)), 180, -180)
    p.drawPath(shackle)
    p.end()
    return px


def lock_icon(locked: bool, widget) -> QIcon:
    color = widget.palette().windowText().color()
    ratio = widget.devicePixelRatioF() if hasattr(widget, "devicePixelRatioF") else 1.0
    return QIcon(lock_pixmap(locked, color, 14, ratio))


def lock_tip(locked: bool, preset: str | None = None) -> str:
    if locked:
        return tr("Locked by the preset «{name}»: unlock it in Presets… to "
                  "change the resolution.", name=preset or "")
    return tr("Unlocked: the resolution can be changed.")


# ---- the Presets window --------------------------------------------------------

class ResolutionPresetsDialog(QDialog):
    """The user's presets: a padlock, a name and a size per row. Apply
    puts one in Width and Height; Save current… keeps the size in the
    fields; a click on a padlock locks or unlocks that preset."""

    COL_LOCK, COL_NAME, COL_W, COL_H = range(4)

    def __init__(self, width: int, height: int, applied: str | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Resolution presets"))
        self.presets = load_presets()
        self.current = (int(width), int(height))
        self.applied = applied              # the preset in use, or None
        self.chosen = None                  # what Apply picked

        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["", tr("Name"), tr("Width"), tr("Height")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(self.COL_LOCK, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.cellDoubleClicked.connect(lambda r, c: (
            None if c == self.COL_LOCK else self.apply_row(r)))
        lay.addWidget(self.table)
        hint = QLabel(tr("Click a padlock to lock or unlock a resolution; "
                         "double-click a row to apply it."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(hint)

        row = QHBoxLayout()
        self.apply_btn = QPushButton(tr("Apply"))
        self.apply_btn.clicked.connect(lambda: self.apply_row(self._row()))
        save = QPushButton(tr("Save current…"))
        save.setToolTip(tr("Keep the resolution in Width and Height as a "
                           "preset ({w} × {h}).", w=width, h=height))
        save.clicked.connect(self._save_current)
        rename = QPushButton(tr("Rename…"))
        rename.clicked.connect(self._rename)
        delete = QPushButton(tr("Delete"))
        delete.clicked.connect(self._delete)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.reject)
        for b in (self.apply_btn, save, rename, delete):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(460, 340)
        self._fill()

    # ---- the table -----------------------------------------------------------
    def _fill(self, select: str | None = None) -> None:
        self.table.setRowCount(len(self.presets))
        for r, p in enumerate(self.presets):
            lock = QTableWidgetItem()
            lock.setIcon(lock_icon(p["locked"], self))
            lock.setToolTip(tr("Locked: click to unlock") if p["locked"]
                            else tr("Unlocked: click to lock"))
            self.table.setItem(r, self.COL_LOCK, lock)
            self.table.setItem(r, self.COL_NAME, QTableWidgetItem(p["name"]))
            for col, key in ((self.COL_W, "width"), (self.COL_H, "height")):
                it = QTableWidgetItem(f"{p[key]} px")
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, col, it)
        name = select or self.applied
        for r, p in enumerate(self.presets):
            if p["name"] == name:
                self.table.selectRow(r)
                break

    def _row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _cell_clicked(self, row: int, col: int) -> None:
        if col == self.COL_LOCK:
            self.toggle_lock(row)

    # ---- actions -------------------------------------------------------------
    def toggle_lock(self, row: int) -> None:
        if not 0 <= row < len(self.presets):
            return
        self.presets[row]["locked"] = not self.presets[row]["locked"]
        save_presets(self.presets)
        self._fill(self.presets[row]["name"])

    def apply_row(self, row: int) -> None:
        if not 0 <= row < len(self.presets):
            return
        self.chosen = dict(self.presets[row])
        self.accept()

    def add_preset(self, name: str) -> bool:
        """Keep the current size as ``name`` (unlocked); False when the name
        is empty or taken."""
        name = (name or "").strip()
        if not name or find_preset(self.presets, name) is not None:
            return False
        w, h = self.current
        self.presets.append({"name": name, "width": w, "height": h,
                             "locked": False})
        save_presets(self.presets)
        self._fill(name)
        return True

    def _save_current(self) -> None:
        w, h = self.current
        name, ok = QInputDialog.getText(
            self, tr("Save current…"), tr("Preset name:"),
            text=f"{w} × {h}")
        if ok and not self.add_preset(name) and name.strip():
            QMessageBox.information(self, tr("Resolution presets"), tr(
                "A preset named «{name}» already exists.", name=name.strip()))

    def rename_row(self, row: int, name: str) -> bool:
        name = (name or "").strip()
        if not 0 <= row < len(self.presets) or not name:
            return False
        if any(i != row and p["name"] == name
               for i, p in enumerate(self.presets)):
            return False
        old = self.presets[row]["name"]
        self.presets[row]["name"] = name
        if self.applied == old:
            self.applied = name
        save_presets(self.presets)
        self._fill(name)
        return True

    def _rename(self) -> None:
        row = self._row()
        if row < 0:
            return
        name, ok = QInputDialog.getText(
            self, tr("Rename…"), tr("Preset name:"),
            text=self.presets[row]["name"])
        if ok and not self.rename_row(row, name) and name.strip():
            QMessageBox.information(self, tr("Resolution presets"), tr(
                "A preset named «{name}» already exists.", name=name.strip()))

    def delete_row(self, row: int) -> None:
        if not 0 <= row < len(self.presets):
            return
        del self.presets[row]
        save_presets(self.presets)
        self._fill()

    def _delete(self) -> None:
        self.delete_row(self._row())
