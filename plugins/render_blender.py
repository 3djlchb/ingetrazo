# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Render with Blender (issue #181) — the «Render» tab of the side tray.

A photorealistic image of the current view, made by the user's own Blender
in the background: Cycles for quality, EEVEE for speed. The model goes over
as GLB with the viewport's camera; the light is the sun of the Shadows panel
by day, an even sky when overcast, or — at night — the point lights and
spots placed in this panel. The image comes back into the panel to look at
closely and save. Blender is not bundled: it is found where it is installed,
and when it is not, the panel says how to get it for this package.

An extension through ``setup(app)`` (views/extension_api.py), like Levels:
the lights and the ambience live in the document (saved in the .igz, every
change one undo step), the tab sits beside Properties / Terrain, and the
lights are drawn over the viewport. The logic is in ``core/render_blender.py``
and the Blender side in ``resources/blender/render_scene.py``.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import (QPointF, QProcess, QProcessEnvironment,
                            QSettings, Qt, QUrl)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QPainter, QPen,
                           QPixmap, QVector3D)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import render_blender as rb
from core.i18n import tr
from views.fold_section import FoldSection, narrow, wrapping_form

_SETTINGS = "render/"


def _work_dir() -> Path:
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericCacheLocation)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(base) / "IngeTrazo" / "render" / stamp


# ---- The document's lights and ambience ---------------------------------------

def _state(app) -> dict:
    """``{"ambience", "sun_scale", "lights"}`` from the document, cleaned."""
    raw = app.document_data({}) or {}
    if not isinstance(raw, dict):
        raw = {}
    amb = raw.get("ambience", "day")
    try:
        sun = max(0.1, min(2.0, float(raw.get("sun_scale", 1.0))))
    except (TypeError, ValueError):
        sun = 1.0
    return {"ambience": amb if amb in rb.AMBIENCES else "day",
            "sun_scale": sun,
            "lights": rb.clean_lights(raw.get("lights", []))}


def _store(app, st: dict) -> None:
    """Write back — one undo step, the document becomes unsaved."""
    app.set_document_data({"ambience": st["ambience"],
                           "sun_scale": st["sun_scale"],
                           "lights": st["lights"]})


def _kelvin_groove() -> str:
    """The temperature slider's groove painted with the colours it picks."""
    stops = []
    for i in range(9):
        k = rb.MIN_KELVIN + (rb.MAX_KELVIN - rb.MIN_KELVIN) * i / 8
        r, g, b = rb.kelvin_to_rgb(k)
        stops.append(f"stop:{i / 8:.3f} rgb({int(r * 255)},{int(g * 255)},"
                     f"{int(b * 255)})")
    return ("QSlider::groove:horizontal { height: 8px; border-radius: 4px; "
            "border: 1px solid palette(mid); background: qlineargradient("
            "x1:0, y1:0, x2:1, y2:0, " + ", ".join(stops) + "); }"
            "QSlider::handle:horizontal { width: 12px; margin: -5px 0; "
            "border-radius: 6px; background: palette(button); "
            "border: 1px solid palette(dark); }")


def _kelvin_name(k: int) -> str:
    """What a temperature looks like, in words."""
    if k < 2200:
        return tr("Candle, fire")
    if k < 3200:
        return tr("Warm — a house, a street lamp")
    if k < 4500:
        return tr("Neutral white")
    if k < 7000:
        return tr("Daylight")
    return tr("Cool — a blue sky")


# ---- A larger look at the image -------------------------------------------------

class _ZoomView(QGraphicsView):
    """The finished image: the wheel zooms around the cursor, a drag pans."""

    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem(pixmap)
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(Qt.darkGray)

    def fit(self) -> None:
        self.fitInView(self._item, Qt.KeepAspectRatio)

    def actual_size(self) -> None:
        self.resetTransform()

    def wheelEvent(self, ev) -> None:
        step = 1.25 if ev.angleDelta().y() > 0 else 0.8
        scale = self.transform().m11() * step
        if 0.02 < scale < 16.0:
            self.scale(step, step)


class ImageViewer(QDialog):
    """A larger look at a render: fit, 100 %, zoom and pan."""

    def __init__(self, path: Path, save, parent=None, folder=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Render") + f" — {path.name}")
        self.resize(1200, 800)
        pix = QPixmap(str(path))
        lay = QVBoxLayout(self)
        self.view = _ZoomView(pix, self)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        buttons = [(tr("Fit to window"), self.view.fit),
                   (tr("100 %"), self.view.actual_size),
                   (tr("Save image…"), save)]
        if folder is not None:
            buttons.append((tr("Open folder"), folder))
        for label, slot in buttons:
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(QLabel(tr("Wheel: zoom · drag: move · {w} × {h} px",
                                w=pix.width(), h=pix.height())))
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.view.fit()


# ---- Clicking a light into place ----------------------------------------------

def _make_pick_tool(prompt: str, done, cancelled):
    """A one-click tool: ``done(point)`` with the SNAPPED point (a post tip,
    a ceiling, an endpoint), ``cancelled()`` on Esc. Built here and not at
    module level: a module-level Tool would get its own Extensions-menu
    entry (core/extensions.py), and the panel is the only way in."""
    from tools.base import Tool

    class _PickPoint(Tool):
        name = prompt
        shortcut = None
        uses_snap = True

        def __init__(self) -> None:
            self.hover_point = None

        def on_activate(self, viewport) -> None:
            self.hover_point = None

        def on_deactivate(self, viewport) -> None:
            self.hover_point = None

        def on_hover(self, ctx) -> None:
            self.hover_point = ctx.world
            ctx.viewport.update()

        def on_click(self, ctx) -> None:
            face = ctx.snap is not None and ctx.snap.kind == "on_face"
            done(QVector3D(ctx.world), face)

        def on_key(self, viewport, key, modifiers) -> bool:
            if key == Qt.Key_Escape:
                cancelled()
                return True
            return False

        def rubber_band_lines(self):
            return []

    return _PickPoint()


# ---- The panel ---------------------------------------------------------------------

def _Section(title: str, key: str, parent=None) -> FoldSection:
    """A folding section whose open state is kept under ``render/``."""
    return FoldSection(title, _SETTINGS + "open_" + key, parent)


_narrow = narrow
_form = wrapping_form


class RenderPanel(QWidget):
    """Blender, image settings, ambience, lights and the render itself —
    top to bottom, sized for the side tray."""

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._proc: QProcess | None = None
        self._work: Path | None = None
        self._image: Path | None = None
        self._viewer = None
        self._log: list = []
        self._picking = None            # what the next click is for
        self._saved = str(QSettings().value(rb.SETTINGS_KEY, "") or "")
        self._found = rb.find_blender(self._saved or None)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Never wider than the tray: the controls shrink instead (#181).
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        body.setMinimumWidth(0)
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 6, 8, 8)
        st = QSettings()

        # -- Blender
        sec = _Section(tr("Blender"), "blender")
        bl = QVBoxLayout(sec.body)
        row = QHBoxLayout()
        self._where = QLabel()
        self._where.setWordWrap(True)
        self._where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        _narrow(self._where)
        row.addWidget(self._where, 1)
        pick = QPushButton(tr("Choose…"))
        pick.clicked.connect(self._pick_blender)
        row.addWidget(pick)
        bl.addLayout(row)
        self._help = QFrame()
        self._help.setFrameShape(QFrame.StyledPanel)
        self._help_lay = QVBoxLayout(self._help)
        bl.addWidget(self._help)
        lay.addWidget(sec)

        # -- Image
        sec = _Section(tr("Image"), "image")
        form = _form(sec.body)
        self._engine = QComboBox()
        self._engine.addItem(tr("EEVEE — fast"), "eevee")
        self._engine.addItem(tr("Cycles — best quality"), "cycles")
        self._engine.setCurrentIndex(
            max(0, self._engine.findData(st.value(_SETTINGS + "engine",
                                                  "eevee"))))
        form.addRow(tr("Engine:"), self._engine)
        self._quality = QComboBox()
        for label in (tr("Draft"), tr("Medium"), tr("High")):
            self._quality.addItem(label)
        self._quality.setCurrentIndex(int(st.value(_SETTINGS + "quality", 1)))
        form.addRow(tr("Quality:"), self._quality)
        self._width = QSpinBox()
        self._width.setRange(320, 7680)
        self._width.setSingleStep(160)
        self._width.setValue(int(st.value(_SETTINGS + "width", 1920)))
        self._width.setSuffix(" px")
        self._width.valueChanged.connect(self._update_height)
        wrow = QHBoxLayout()
        self._height = QLabel()
        self._height.setToolTip(tr("The height follows the view's "
                                   "proportions"))
        wrow.addWidget(self._width, 1)
        wrow.addWidget(self._height)
        form.addRow(tr("Width:"), wrow)
        self._ground = QCheckBox(tr("Ground that catches the shadows"))
        self._ground.setChecked(
            str(st.value(_SETTINGS + "ground", "1")) != "0")
        form.addRow(self._ground)
        self._blend = QCheckBox(tr("Also keep the .blend file"))
        self._blend.setToolTip(tr("To retouch the scene in Blender "
                                  "afterwards"))
        self._blend.setChecked(str(st.value(_SETTINGS + "blend", "0")) == "1")
        form.addRow(self._blend)
        _narrow(self._engine, self._quality, self._width, self._ground,
                self._blend)
        lay.addWidget(sec)

        # -- Ambience
        sec = _Section(tr("Ambience"), "ambience")
        form = _form(sec.body)
        self._ambience = QComboBox()
        for key, label in (("day", tr("Day — the sun of the Shadows panel")),
                           ("night", tr("Night — your lights")),
                           ("overcast", tr("Overcast — soft light"))):
            self._ambience.addItem(label, key)
        self._ambience.activated.connect(self._on_ambience)
        form.addRow(self._ambience)
        self._sun = QLabel()
        self._sun.setWordWrap(True)
        form.addRow(self._sun)
        # The day's sun: how strong, and — in the Shadows panel — when.
        self._sun_box = QWidget()
        sf = _form(self._sun_box)
        sf.setContentsMargins(0, 0, 0, 0)
        srow = QHBoxLayout()
        self._sun_scale = QSlider(Qt.Horizontal)
        self._sun_scale.setRange(10, 200)
        self._sun_scale.setSingleStep(5)
        self._sun_scale.setPageStep(25)
        self._sun_scale_lbl = QLabel()
        self._sun_scale_lbl.setMinimumWidth(44)
        self._sun_scale.valueChanged.connect(
            lambda v: self._sun_scale_lbl.setText(f"{v} %"))
        self._sun_scale.sliderReleased.connect(self._on_sun_scale)
        srow.addWidget(self._sun_scale, 1)
        srow.addWidget(self._sun_scale_lbl)
        sf.addRow(tr("Sun strength:"), srow)
        when = QPushButton(tr("Change date and time…"))
        when.setToolTip(tr("The Shadows panel: with the sun low, the sky "
                           "turns to a sunset"))
        when.clicked.connect(self._open_shadows)
        sf.addRow(when)
        form.addRow(self._sun_box)
        _narrow(self._ambience, self._sun, self._sun_scale, when)
        lay.addWidget(sec)

        # -- Lights
        sec = _Section(tr("Lights"), "lights")
        ll = QVBoxLayout(sec.body)
        self._lights = QListWidget()
        self._lights.setMinimumHeight(70)
        self._lights.setMaximumHeight(150)
        self._lights.currentRowChanged.connect(self._on_light_selected)
        self._lights.itemChanged.connect(self._on_light_checked)
        ll.addWidget(self._lights)
        row = QHBoxLayout()
        add_point = QPushButton(tr("+ Point light"))
        add_point.clicked.connect(lambda: self._begin_pick("new", "point"))
        add_spot = QPushButton(tr("+ Spot"))
        add_spot.clicked.connect(lambda: self._begin_pick("new", "spot"))
        self._del = QPushButton(tr("Delete"))
        self._del.clicked.connect(self._delete_light)
        for b in (add_point, add_spot):
            row.addWidget(b)
        _narrow(add_point, add_spot)
        ll.addLayout(row)
        self._editor = QWidget()
        ef = _form(self._editor)
        ef.setContentsMargins(0, 0, 0, 0)
        # Colour as a temperature: a slider over the black-body gradient,
        # from a candle to a blue sky, with the kelvins beside it.
        krow = QHBoxLayout()
        self._kelvin = QSlider(Qt.Horizontal)
        self._kelvin.setRange(rb.MIN_KELVIN, rb.MAX_KELVIN)
        self._kelvin.setSingleStep(100)
        self._kelvin.setPageStep(500)
        self._kelvin.setStyleSheet(_kelvin_groove())
        self._kelvin_spin = QSpinBox()
        self._kelvin_spin.setRange(rb.MIN_KELVIN, rb.MAX_KELVIN)
        self._kelvin_spin.setSingleStep(100)
        self._kelvin_spin.setSuffix(" K")
        self._kelvin.valueChanged.connect(self._kelvin_spin.setValue)
        self._kelvin_spin.valueChanged.connect(self._kelvin.setValue)
        self._kelvin_spin.valueChanged.connect(self._name_kelvin)
        self._kelvin.sliderReleased.connect(self._on_light_edited)
        self._kelvin_spin.editingFinished.connect(self._on_light_edited)
        krow.addWidget(self._kelvin, 1)
        krow.addWidget(self._kelvin_spin)
        ef.addRow(tr("Colour:"), krow)
        self._kelvin_name = QLabel()
        self._kelvin_name.setStyleSheet("color: palette(mid);")
        ef.addRow("", self._kelvin_name)
        self._power = QDoubleSpinBox()
        self._power.setRange(1.0, 100000.0)
        self._power.setDecimals(0)
        self._power.setSingleStep(50.0)
        self._power.setSuffix(" W")
        self._power.editingFinished.connect(self._on_light_edited)
        ef.addRow(tr("Power:"), self._power)
        self._angle = QDoubleSpinBox()
        self._angle.setRange(5.0, 175.0)
        self._angle.setDecimals(0)
        self._angle.setSuffix(" °")
        self._angle.editingFinished.connect(self._on_light_edited)
        self._angle_label = QLabel(tr("Opening:"))
        ef.addRow(self._angle_label, self._angle)
        # A spot's aim in numbers: its compass heading and how far it
        # tilts down (90° = straight down); Aim… does it with a click.
        self._heading = QDoubleSpinBox()
        self._heading.setRange(0.0, 359.0)
        self._heading.setDecimals(0)
        self._heading.setSingleStep(15.0)
        self._heading.setWrapping(True)
        self._heading.setSuffix(" °")
        self._heading.setToolTip(tr("0° north (green axis), 90° east (red "
                                    "axis)"))
        self._heading.editingFinished.connect(self._on_aim_edited)
        self._heading_label = QLabel(tr("Heading:"))
        ef.addRow(self._heading_label, self._heading)
        self._tilt = QDoubleSpinBox()
        self._tilt.setRange(-90.0, 90.0)
        self._tilt.setDecimals(0)
        self._tilt.setSingleStep(15.0)
        self._tilt.setSuffix(" °")
        self._tilt.setToolTip(tr("90° straight down, 0° level, negative "
                                 "upward"))
        self._tilt.editingFinished.connect(self._on_aim_edited)
        self._tilt_label = QLabel(tr("Tilt down:"))
        ef.addRow(self._tilt_label, self._tilt)
        row = QHBoxLayout()
        self._move = QPushButton(tr("Place again"))
        self._move.clicked.connect(lambda: self._begin_pick("move"))
        self._aim = QPushButton(tr("Aim…"))
        self._aim.setToolTip(tr("Click the point the spot looks at"))
        self._aim.clicked.connect(lambda: self._begin_pick("aim"))
        row.addWidget(self._move)
        row.addWidget(self._aim)
        row.addWidget(self._del)
        ef.addRow(row)
        _narrow(self._kelvin, self._power, self._angle, self._heading,
                self._tilt, self._move, self._aim, self._del,
                self._kelvin_name)
        ll.addWidget(self._editor)
        hint = QLabel(tr("Click on the model to place a light — on a lamp "
                         "post, a ceiling, a bench. At night they are the "
                         "only light besides a faint moon."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        _narrow(hint)
        ll.addWidget(hint)
        lay.addWidget(sec)

        # -- Render: the button, the progress and a word on how it went.
        # The finished image opens in its own window, which has Save and
        # Open folder (Marco: those belong with the image, not the tray);
        # the status line keeps a link back to it.
        sec = _Section(tr("Render"), "render")
        rl = QVBoxLayout(sec.body)
        self._go = QPushButton(tr("Render"))
        self._go.clicked.connect(self._start)
        self._stop = QPushButton(tr("Cancel render"))
        self._stop.clicked.connect(self._cancel)
        self._stop.setVisible(False)
        _narrow(self._go, self._stop)
        rl.addWidget(self._go)
        rl.addWidget(self._stop)
        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setVisible(False)
        rl.addWidget(self._bar)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.RichText)
        self._status.linkActivated.connect(lambda _l: self._open_viewer())
        _narrow(self._status)
        rl.addWidget(self._status)
        lay.addWidget(sec)
        lay.addStretch(1)

        self._update_height()
        self._update_where()
        self.refresh_lights()

    # ---- Blender -------------------------------------------------------------
    def _update_where(self) -> None:
        kind = rb.package_kind()
        if self._found is not None and kind != "snap":
            self._where.setText(self._found.where)
            self._go.setEnabled(self._proc is None)
            self._help.setVisible(False)
            return
        self._go.setEnabled(False)
        self._where.setText(tr("Blender is not installed, or IngeTrazo "
                               "cannot reach it."))
        self._fill_help(kind)

    def _fill_help(self, kind: str) -> None:
        """The steps for this package, each command ready to copy."""
        while self._help_lay.count():
            w = self._help_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        title = QLabel("<b>" + tr("How to render with Blender") + "</b>")
        self._help_lay.addWidget(title)
        for text, cmd in rb.install_steps(kind):
            lbl = QLabel(tr(text))
            lbl.setWordWrap(True)
            self._help_lay.addWidget(lbl)
            if cmd:
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                field = QLineEdit(cmd)
                field.setReadOnly(True)
                field.setCursorPosition(0)
                copy = QPushButton(tr("Copy"))
                copy.clicked.connect(
                    lambda _=False, c=cmd: QApplication.clipboard().setText(c))
                h.addWidget(field, 1)
                h.addWidget(copy)
                self._help_lay.addWidget(row)
        if kind != "snap":
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            web = QPushButton(tr("Open blender.org"))
            web.clicked.connect(lambda: QDesktopServices.openUrl(
                QUrl(rb.DOWNLOAD_URL)))
            again = QPushButton(tr("Search again"))
            again.clicked.connect(self._search_again)
            h.addWidget(web)
            h.addWidget(again)
            h.addStretch(1)
            self._help_lay.addWidget(row)
        self._help.setVisible(True)

    def _search_again(self) -> None:
        self._found = rb.find_blender(self._saved or None)
        self._update_where()
        self._status.setText(tr("Blender found.") if self._found else tr(
            "Still no Blender. When it is installed, press Search again."))

    def _pick_blender(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Where is Blender?"), "",
            tr("Blender (blender blender.exe Blender);;All files (*)"))
        if not path:
            return
        QSettings().setValue(rb.SETTINGS_KEY, path)
        self._saved = path
        self._found = rb.find_blender(path)
        self._update_where()

    # ---- Image settings ------------------------------------------------------
    def _aspect(self) -> float:
        vp = self.app.viewport
        return max(vp.width(), 1) / max(vp.height(), 1)

    def _update_height(self) -> None:
        h = max(2, round(self._width.value() / self._aspect()))
        self._height.setText(f"× {h} px")

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._update_height()          # the view may have been resized
        self._update_sun()

    def _remember_settings(self) -> None:
        st = QSettings()
        st.setValue(_SETTINGS + "engine", self._engine.currentData())
        st.setValue(_SETTINGS + "quality", self._quality.currentIndex())
        st.setValue(_SETTINGS + "width", self._width.value())
        st.setValue(_SETTINGS + "ground", "1" if self._ground.isChecked()
                    else "0")
        st.setValue(_SETTINGS + "blend", "1" if self._blend.isChecked()
                    else "0")

    # ---- Ambience ------------------------------------------------------------
    def _on_ambience(self, _i) -> None:
        st = _state(self.app)
        st["ambience"] = self._ambience.currentData()
        _store(self.app, st)
        self._update_sun()

    def _on_sun_scale(self) -> None:
        st = _state(self.app)
        value = self._sun_scale.value() / 100.0
        if abs(st["sun_scale"] - value) > 1e-6:
            st["sun_scale"] = value
            _store(self.app, st)

    def _open_shadows(self) -> None:
        """Drop the Shadows panel down from its toolbar button, where the
        date and the time of the sun are set."""
        from PySide6.QtWidgets import QToolButton
        for btn in self.app.window.findChildren(QToolButton):
            if btn.toolTip() == tr("Shadows") and btn.menu() is not None:
                btn.showMenu()
                return

    def _update_sun(self) -> None:
        amb = self._ambience.currentData()
        sh = getattr(self.app.scene, "shadows", None)
        self._sun_box.setVisible(amb == "day")
        scale = int(round(_state(self.app)["sun_scale"] * 100))
        self._sun_scale.blockSignals(True)
        self._sun_scale.setValue(scale)
        self._sun_scale.blockSignals(False)
        self._sun_scale_lbl.setText(f"{scale} %")
        if amb == "night":
            n = sum(1 for lt in _state(self.app)["lights"] if lt["on"])
            self._sun.setText(tr("No sun: a dark sky, a faint moon and your "
                                 "{n} light(s) switched on.", n=n))
        elif amb == "overcast":
            self._sun.setText(tr("No sun: an even grey sky, soft shadows."))
        elif rb.sun_toward(self.app.scene) is None:
            self._sun.setText(tr("Night at the date and time of the Shadows "
                                 "panel: sky light only."))
        elif sh is not None:
            self._sun.setText(tr(
                "{d:02d}/{m:02d} at {h:02d}:{mi:02d}, from the Shadows panel.",
                d=sh.day, m=sh.month, h=sh.hour, mi=sh.minute))

    # ---- Lights --------------------------------------------------------------
    def selected_index(self):
        row = self._lights.currentRow()
        return row if 0 <= row < self._lights.count() else None

    def refresh_lights(self) -> None:
        """Show what the document holds (after an edit, an undo, Open)."""
        st = _state(self.app)
        i = self._ambience.findData(st["ambience"])
        self._ambience.blockSignals(True)
        self._ambience.setCurrentIndex(max(0, i))
        self._ambience.blockSignals(False)
        row = self._lights.currentRow()
        self._lights.blockSignals(True)
        self._lights.clear()
        for n, lt in enumerate(st["lights"], 1):
            kind = tr("Spot") if lt["kind"] == "spot" else tr("Point light")
            label = lt["name"] or f"{kind} {n}"
            item = QListWidgetItem(f"{label} · {int(lt['power'])} W")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if lt["on"] else Qt.Unchecked)
            self._lights.addItem(item)
        if self._lights.count():
            self._lights.setCurrentRow(min(max(row, 0),
                                           self._lights.count() - 1))
        self._lights.blockSignals(False)
        self._on_light_selected(self._lights.currentRow())
        self._update_sun()
        self.app.viewport.update()

    def _on_light_selected(self, row: int) -> None:
        lights = _state(self.app)["lights"]
        has = 0 <= row < len(lights)
        self._editor.setEnabled(has)
        self._del.setEnabled(has)
        if not has:
            self.app.viewport.update()
            return
        lt = lights[row]
        k = lt["kelvin"] if lt["kelvin"] is not None else rb.DEFAULT_KELVIN
        for w in (self._kelvin, self._kelvin_spin):
            w.blockSignals(True)
        self._kelvin.setValue(k)
        self._kelvin_spin.setValue(k)
        for w in (self._kelvin, self._kelvin_spin):
            w.blockSignals(False)
        self._name_kelvin(k)
        self._power.setValue(lt["power"])
        self._angle.setValue(lt["angle"])
        heading, tilt = rb.angles_from_aim(lt["dir"])
        self._heading.setValue(heading)
        self._tilt.setValue(tilt)
        spot = lt["kind"] == "spot"
        for w in (self._angle, self._angle_label, self._aim, self._heading,
                  self._heading_label, self._tilt, self._tilt_label):
            w.setVisible(spot)
        self.app.viewport.update()

    def _name_kelvin(self, k: int) -> None:
        self._kelvin_name.setText(_kelvin_name(int(k)))

    def _on_aim_edited(self) -> None:
        i = self.selected_index()
        st = _state(self.app)
        if i is None or i >= len(st["lights"]):
            return
        d = rb.aim_from_angles(self._heading.value(), self._tilt.value())
        lt = st["lights"][i]
        if any(abs(a - b) > 1e-6 for a, b in zip(
                d, [c / (math.hypot(*lt["dir"]) or 1.0) for c in lt["dir"]])):
            lt["dir"] = d
            _store(self.app, st)

    def _on_light_checked(self, item) -> None:
        row = self._lights.row(item)
        st = _state(self.app)
        if 0 <= row < len(st["lights"]):
            st["lights"][row]["on"] = item.checkState() == Qt.Checked
            _store(self.app, st)

    def _on_light_edited(self, *_a) -> None:
        i = self.selected_index()
        st = _state(self.app)
        if i is None or i >= len(st["lights"]):
            return
        lt = st["lights"][i]
        k = int(self._kelvin_spin.value())
        new = dict(lt, kelvin=k, color=list(rb.kelvin_to_rgb(k)),
                   power=float(self._power.value()),
                   angle=float(self._angle.value()))
        if new != lt:
            st["lights"][i] = new
            _store(self.app, st)

    def _delete_light(self) -> None:
        i = self.selected_index()
        st = _state(self.app)
        if i is not None and i < len(st["lights"]):
            del st["lights"][i]
            _store(self.app, st)

    def add_light(self, kind: str, point, on_face: bool = False) -> None:
        """A new light at ``point``: a point light a hand above a face it
        was clicked on (not buried in it); a spot aiming straight down."""
        st = _state(self.app)
        pos = [point.x(), point.y(), point.z() + (0.1 if on_face
                                                  and kind == "point" else 0.0)]
        st["lights"].append({
            "kind": kind, "pos": pos, "dir": [0.0, 0.0, -1.0],
            "kelvin": rb.DEFAULT_KELVIN,
            "power": rb.DEFAULT_POWER[kind], "angle": 60.0, "on": True,
            "name": ""})
        _store(self.app, st)
        self._lights.setCurrentRow(len(st["lights"]) - 1)

    def _begin_pick(self, purpose: str, kind: str | None = None) -> None:
        if purpose != "new" and self.selected_index() is None:
            return
        self._picking = (purpose, kind)
        prompts = {"new": tr("Click where the light goes"),
                   "move": tr("Click the light's new place"),
                   "aim": tr("Click the point the spot looks at")}
        tool = _make_pick_tool(prompts[purpose], self._picked, self._end_pick)
        vp = self.app.viewport
        vp.set_active_tool(tool)
        win = self.app.window
        win.statusBar().showMessage(prompts[purpose] + " — " +
                                    tr("Esc cancels"))
        vp.setFocus()

    def _picked(self, point, on_face: bool) -> None:
        purpose, kind = self._picking or (None, None)
        i = self.selected_index()
        if purpose == "new":
            self.add_light(kind, point, on_face)
        elif purpose in ("move", "aim") and i is not None:
            st = _state(self.app)
            lt = st["lights"][i]
            if purpose == "move":
                lt["pos"] = [point.x(), point.y(), point.z()]
            else:
                d = [point.x() - lt["pos"][0], point.y() - lt["pos"][1],
                     point.z() - lt["pos"][2]]
                if math.hypot(*d) > 1e-6:
                    lt["dir"] = d
            _store(self.app, st)
        self._end_pick()

    def _end_pick(self) -> None:
        self._picking = None
        win = self.app.window
        win.statusBar().clearMessage()
        activate = getattr(win, "_activate_tool", None)
        if callable(activate):
            activate("select")

    # ---- Rendering -----------------------------------------------------------
    def _start(self) -> None:
        if self._found is None or self._proc is not None:
            return
        self._remember_settings()
        st = _state(self.app)
        self._work = _work_dir()
        try:
            job = rb.write_job(
                self.app.scene, self.app.viewport.camera, self._work,
                engine=self._engine.currentData(),
                quality=self._quality.currentIndex(),
                width=self._width.value(),
                height=max(2, round(self._width.value() / self._aspect())),
                ground=self._ground.isChecked(),
                keep_blend=self._blend.isChecked(),
                ambience=st["ambience"], lights=st["lights"],
                sun_scale=st["sun_scale"])
        except Exception as exc:  # noqa: BLE001 - say it, do not crash
            QMessageBox.critical(self, tr("Render with Blender"), str(exc))
            return
        argv = rb.command(self._found, job)
        proc = QProcess(self)
        env = QProcessEnvironment()
        for k, v in rb.clean_env().items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._failed_to_start)
        self._proc = proc
        self._log = []
        self._image = None
        self._set_running(True)
        cycles = self._engine.currentData() == "cycles"
        self._status.setText(
            tr("Blender is preparing the scene…") + (
                "<br>" + tr("The first Cycles render on a graphics card can "
                          "take a few minutes while Blender prepares it; "
                          "the next ones are fast.") if cycles else ""))
        proc.start(argv[0], argv[1:])

    def _set_running(self, running: bool) -> None:
        self._bar.setVisible(running)
        self._bar.setValue(0)
        self._go.setVisible(not running)
        self._stop.setVisible(running)
        for w in (self._engine, self._quality, self._width, self._ground,
                  self._blend):
            w.setEnabled(not running)

    def _read(self) -> None:
        if self._proc is None:
            return
        text = bytes(self._proc.readAllStandardOutput()).decode(
            "utf-8", "replace")
        for line in text.splitlines():
            self._log.append(line)
            frac = rb.progress_of(line)
            if frac is not None:
                self._bar.setValue(int(frac * 1000))
                self._status.setText(tr("Rendering… {p} %",
                                        p=int(frac * 100)))
            elif "Loading render kernels" in line or "Compiling" in line:
                self._status.setText(tr("Blender is preparing the graphics "
                                        "card (only the first time)…"))
        self._log = self._log[-400:]

    def _failed_to_start(self, error) -> None:
        if error == QProcess.FailedToStart:
            self._proc = None
            self._set_running(False)
            self._status.setText(tr("Blender could not be started."))

    def _finished(self, code: int, _status) -> None:
        self._proc = None
        self._set_running(False)
        out = (self._work / "render.png") if self._work else None
        if code == 0 and out is not None and out.is_file():
            self._image = out
            self._status.setText(tr("Done.") + ' <a href="#show">'
                                 + tr("Show image") + "</a>")
            self._open_viewer()
            return
        import html
        tail = "\n".join(line for line in self._log[-12:] if line.strip())
        self._status.setText(html.escape(
            tr("Blender stopped without an image.")
            + ("\n\n" + tail if tail else "")).replace("\n", "<br>"))

    def _open_viewer(self) -> None:
        """The image in its own window — one, reused: a second render
        replaces the picture instead of piling up windows."""
        if self._image is None:
            return
        old = self._viewer
        if old is not None:
            old.close()
        self._viewer = ImageViewer(self._image, self._save_image,
                                   self.window(), folder=self._open_folder)
        self._viewer.setAttribute(Qt.WA_DeleteOnClose)
        self._viewer.destroyed.connect(self._forget_viewer)
        self._viewer.show()
        self._viewer.raise_()

    def _forget_viewer(self, *_a) -> None:
        self._viewer = None

    def _cancel(self) -> None:
        if self._proc is not None:
            self._proc.kill()

    def _save_image(self) -> None:
        if self._image is None:
            return
        doc = getattr(self.app.window, "_current_path", None)
        start = (str(Path(doc).with_name(Path(doc).stem + "-render.png"))
                 if doc else "render.png")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save image…"), start, tr("PNG image (*.png)"))
        if path:
            import shutil
            shutil.copyfile(self._image, path)

    def _open_folder(self) -> None:
        if self._work is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._work)))


# ---- The lights over the viewport ------------------------------------------------

def draw_lights(app, panel, viewport, painter) -> None:
    """A small bulb at each light (tinted by its colour, grey when off), a
    stroke along a spot's aim, the selected one ringed and named."""
    lights = _state(app)["lights"]
    if not lights:
        return
    selected = panel.selected_index()
    font = QFont()
    font.setPointSize(8)
    painter.setFont(font)
    painter.setRenderHint(QPainter.Antialiasing)
    for i, lt in enumerate(lights):
        p = QVector3D(*lt["pos"])
        px = viewport._world_to_pixel(p)
        if px is None:
            continue
        c = QPointF(px[0], px[1])
        tint = (QColor.fromRgbF(*lt["color"]) if lt["on"]
                else QColor(150, 150, 150))
        if lt["kind"] == "spot":
            d = QVector3D(*lt["dir"]).normalized()
            tip = viewport._world_to_pixel(p + d * 0.8)
            if tip is not None:
                painter.setPen(QPen(tint.darker(130), 1.6, Qt.DashLine))
                painter.drawLine(c, QPointF(tip[0], tip[1]))
        painter.setPen(QPen(QColor(40, 40, 40), 1.2))
        painter.setBrush(tint)
        painter.drawEllipse(c, 6.0, 6.0)
        painter.setPen(QPen(tint.darker(140), 1.4))
        for k in range(8):
            a = k * math.pi / 4
            painter.drawLine(QPointF(c.x() + 8 * math.cos(a),
                                     c.y() + 8 * math.sin(a)),
                             QPointF(c.x() + 11 * math.cos(a),
                                     c.y() + 11 * math.sin(a)))
        if i == selected:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(54, 137, 230), 2.0))
            painter.drawEllipse(c, 14.0, 14.0)
            kind = tr("Spot") if lt["kind"] == "spot" else tr("Point light")
            painter.setPen(QPen(QColor(20, 20, 20)))
            painter.drawText(QPointF(c.x() + 16, c.y() - 10),
                             f"{lt['name'] or kind} · {int(lt['power'])} W")


# ---- Entry point -------------------------------------------------------------------

def setup(app) -> None:
    panel = RenderPanel(app)
    dock = app.add_panel(tr("Render"), panel)
    # The tab is the render tool; this entry only brings it forward —
    # shown again if hidden from Window ▸ Panels (Marco: «no aparece en el
    # menú Extensiones»).
    app.add_menu_action(tr("Render with Blender…"),
                        lambda: app.show_panel(dock))
    app.on_document_changed(panel.refresh_lights)
    app.add_overlay(lambda vp, painter: draw_lights(app, panel, vp, painter))
