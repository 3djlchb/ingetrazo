# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The Animation tab of the side tray.

The document's scenes are the keyframes: tick the ones to animate, capture
a section plane's state per scene if it should move, set the frame rate,
size and length, and render — through the viewport's own renderer or
through Blender — to an image sequence, an MP4 (FFmpeg) or both. Everything
the tab holds is saved in the .igz.

The frames appear in the animation's own frame buffer (``framebuffer``),
which also pauses and cancels the render; what the render does is written
to the tab's Console. The timeline stays in the 3D viewport.
"""
from __future__ import annotations

import json
import os
import shutil
import webbrowser
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (
    QDoubleValidator, QFontDatabase, QIntValidator, QPalette, QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QProgressBar, QProgressDialog, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from core.i18n import tr
from views.fold_section import FoldSection, narrow

from .core import AnimationRender, RenderCancelled, apply_frame, capture_section_state
from .exporter import CODECS, FFmpegExporter
from .ffmpeg_tools import FFmpegManager, remember_ffmpeg
from .framebuffer import CANCELLED, DONE, FAILED, AnimationFrameBuffer
from .persistence import (
    SCHEMA_VERSION, default_plugin_data, get_plugin_data, new_scene_id,
    normalize_plugin_data,
)
from .sampling import (
    ENGINE_CYCLES, PROCESSING_CUSTOM, PROCESSING_NATIVE, describe,
    native_samples, normalize_sampling, request_fields, visible_custom_controls,
)

#: Renderers, by the index the document stores (``settings.image_type``).
RENDER_VIEWPORT, RENDER_BLENDER = 0, 1
#: Output kinds, by the index the document stores (``settings.file_type``).
OUT_IMAGES, OUT_VIDEO, OUT_BOTH = 0, 1, 2
FPS_PRESETS = (6, 8, 12, 23.98, 24, 25, 29.97, 30, 50, 59.94, 60, 120, 240)
#: QSettings key of «Show timeline».
SHOW_TIMELINE_KEY = "igz_animacion/show_timeline"
#: QSettings prefix of the sections' open / folded state.
_SECTION_KEY = "igz_animacion/open_"
QUALITY_NAMES = ("Draft", "Medium", "High")


def _section(title: str, key: str, default_open: bool = True) -> FoldSection:
    """A section that folds like the Render tab's (the app's own
    ``FoldSection``, its state remembered); fill ``body_layout``."""
    sec = FoldSection(title, _SECTION_KEY + key, default_open=default_open)
    sec.body_layout = QVBoxLayout(sec.body)
    sec.body_layout.setContentsMargins(6, 4, 6, 6)
    sec.body_layout.setSpacing(5)
    return sec


def _form_row(label: str, field) -> QWidget:
    """One labelled line as a widget of its own, so it can be hidden whole."""
    w = QWidget()
    form = QFormLayout(w)
    form.setContentsMargins(0, 0, 0, 0)
    form.addRow(label, field)
    return w


#: A break opportunity after each path separator, so a long path wraps in
#: the narrow tray instead of widening it (a path has no spaces to break at).
_ZWSP = "\u200b"


def breakable(text: str) -> str:
    return str(text).replace("\\", "\\" + _ZWSP).replace("/", "/" + _ZWSP)


def fit_to_tray(root: QWidget) -> None:
    """Let every control under ``root`` shrink with the side tray, as the
    Render tab does: the widest of them set the tab's minimum width, and
    the tray then cut off the right-hand side instead of narrowing them."""
    # Labels keep their width: a form's label squeezed under its text ran
    # into the field; long texts wrap (paths at their separators).
    kinds = (QComboBox, QLineEdit, QAbstractSpinBox, QPushButton, QCheckBox,
             QListWidget, QTableWidget, QPlainTextEdit, QProgressBar)
    for w in root.findChildren(QWidget):
        if isinstance(w, kinds):
            if w.maximumWidth() < 100:          # a fixed little «…» button
                continue
            narrow(w)
        elif isinstance(w, QToolButton) and isinstance(w.parent(), FoldSection):
            w.setSizePolicy(QSizePolicy.Ignored, w.sizePolicy().verticalPolicy())
            w.setMinimumWidth(40)
    for form in root.findChildren(QFormLayout):
        # The label goes above its field when both do not fit side by side.
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)


def frame_segment_text(views, frame: int, total: int) -> str:
    """«Scene A → Scene B» for the pair of scenes ``frame`` lies between."""
    views = list(views or [])
    if len(views) < 2:
        return views[0].name if views else ""
    position = (max(1, frame) - 1) / max(1, total - 1)
    segment_float = position * (len(views) - 1)
    segment = min(len(views) - 2, int(segment_float))
    return f"{views[segment].name} → {views[segment + 1].name}"


def _row(*widgets, stretch_first=True) -> QHBoxLayout:
    box = QHBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(4)
    for i, w in enumerate(widgets):
        if isinstance(w, str):
            box.addWidget(QLabel(w))
        else:
            box.addWidget(w, 1 if (i == 0 and stretch_first) else 0)
    return box


class AnimacionPanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.timeline = None
        self.timeline_dock = None
        self.dock = None
        self._animation_data = default_plugin_data()
        self._interpolate_section = True
        self._selection_saved = False
        self._selected_indices: set = set()
        self._loading = False
        self._frame_preview_busy = False
        self._renderer = None
        self._video_name_edited = False
        self._igz_path_edited = False
        # What the panel knows of the document, to tell a real change from
        # its own echo: the scene object (New / Open replace it), the scene
        # list as objects (a scene added, deleted or moved in Properties),
        # and the stored data as text (an undo of a setting).
        self._scene_obj = None
        self._views_snapshot: list = []
        self._stored_json = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._flush_save)
        self._guard = QTimer(self)
        self._guard.setInterval(500)
        self._guard.timeout.connect(self._poll)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._build_renderer(layout)
        self._build_timeline(layout)
        self._build_scenes(layout)
        self._build_settings(layout)
        self._build_output(layout)
        self._build_ffmpeg(layout)
        self._build_render(layout)
        self._build_console(layout)
        layout.addStretch(1)
        self.framebuffer = None

        self.ffmpeg_manager = FFmpegManager()
        self._scene_obj = getattr(self.app, "scene", None)
        self._load_document_data()
        self.refresh_scenes()
        self.refresh_ffmpeg_status()
        self._refresh_output_defaults(force=True)
        self._update_renderer_controls()
        fit_to_tray(body)
        self._guard.start()
        # Every edit reaches here: only a CHEAP check, never a reload (the
        # stand-alone plugin reloaded its whole state on every edit, which
        # froze big models and reset whatever was being typed in the tab).
        self.app.on_document_changed(self._poll)

    # ---- Building the tab ----------------------------------------------------
    def _build_renderer(self, layout):
        sec = _section(tr("Renderer"), "renderer")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.image_type = QComboBox()
        self.image_type.addItem(tr("3D viewport (native)"), RENDER_VIEWPORT)
        self.image_type.addItem("Blender", RENDER_BLENDER)
        # ``activated``: only the user's pick asks for Blender — loading a
        # document or touching a scene used to pop the «Blender not found»
        # box up by itself.
        self.image_type.activated.connect(self._renderer_picked)
        self.image_type.currentIndexChanged.connect(self._renderer_changed)
        form.addRow(tr("Renderer:"), self.image_type)
        sec.body_layout.addLayout(form)

        self.blender_box = QWidget()
        bform = QFormLayout(self.blender_box)
        bform.setContentsMargins(0, 0, 0, 0)
        self.blender_path = QLineEdit()
        self.blender_path.setPlaceholderText(tr("Blender executable"))
        self.blender_path.setReadOnly(True)
        pick = QPushButton("…")
        pick.setFixedWidth(34)
        pick.setToolTip(tr("Choose the Blender executable"))
        pick.clicked.connect(self._choose_blender_executable)
        bform.addRow(tr("Blender:"), _row(self.blender_path, pick))
        self.blender_engine = QComboBox()
        self.blender_engine.addItem("Cycles", "cycles")
        self.blender_engine.addItem("EEVEE", "eevee")
        self.blender_engine.currentIndexChanged.connect(self._blender_settings_changed)
        bform.addRow(tr("Engine:"), self.blender_engine)
        self.processing = QComboBox()
        self.processing.addItem(tr("Native"), PROCESSING_NATIVE)
        self.processing.addItem(tr("Custom settings"), PROCESSING_CUSTOM)
        self.processing.setToolTip(tr(
            "Native: the quality set in the Render tab. Custom: this "
            "animation's own samples."))
        self.processing.currentIndexChanged.connect(self._blender_settings_changed)
        bform.addRow(tr("Processing:"), self.processing)

        # Native: what the Render tab's quality means for this engine.
        self.native_quality = QLabel()
        self.native_samples = QLabel()
        self.native_source = QLabel(tr("Render ▸ Quality"))
        self.native_source.setStyleSheet("font-size: 11px;")
        self.native_rows = [
            _form_row(tr("Quality:"), self.native_quality),
            _form_row(tr("Samples:"), self.native_samples),
            _form_row(tr("Source:"), self.native_source)]
        for w in self.native_rows:
            bform.addRow(w)

        self.noise_threshold_checkbox = QCheckBox(tr("Noise threshold"))
        self.noise_threshold_checkbox.setChecked(True)
        self.noise_threshold_checkbox.setToolTip(tr(
            "Cycles stops sampling a pixel once its noise is under this "
            "threshold (adaptive sampling)."))
        self.noise_threshold = QLineEdit("0.0100")
        self.noise_threshold.setValidator(
            QDoubleValidator(0.0, 1.0e9, 6, self.noise_threshold))
        self.noise_threshold_row = QWidget()
        self.noise_threshold_row.setLayout(_row(self.noise_threshold_checkbox,
                                                self.noise_threshold,
                                                stretch_first=False))
        bform.addRow(self.noise_threshold_row)
        self.max_samples = QLineEdit("128")
        self.max_samples.setValidator(QIntValidator(0, 2147483647, self.max_samples))
        self.min_samples = QLineEdit("0")
        self.min_samples.setValidator(QIntValidator(0, 2147483647, self.min_samples))
        self.samples = QLineEdit("128")
        self.samples.setValidator(QIntValidator(0, 2147483647, self.samples))
        self.time_limit = QLineEdit("0")
        self.time_limit.setValidator(
            QDoubleValidator(0.0, 2147483647.0, 3, self.time_limit))
        self.max_samples_row = _form_row(tr("Max samples:"), self.max_samples)
        self.min_samples_row = _form_row(tr("Min samples:"), self.min_samples)
        self.samples_row = _form_row(tr("Samples:"), self.samples)
        self.time_limit_row = _form_row(tr("Time limit:"), _row(self.time_limit, "s"))
        for w in (self.max_samples_row, self.min_samples_row, self.samples_row,
                  self.time_limit_row):
            bform.addRow(w)
        self.noise_threshold_checkbox.toggled.connect(self._sampling_mode_changed)
        for field in (self.noise_threshold, self.max_samples, self.min_samples,
                      self.time_limit, self.samples):
            field.editingFinished.connect(self._changed)
        sec.body_layout.addWidget(self.blender_box)
        layout.addWidget(sec)

    def _build_timeline(self, layout):
        sec = _section(tr("Timeline"), "timeline")
        self.show_timeline = QCheckBox(tr("Show the timeline"))
        self.show_timeline.setToolTip(tr(
            "The animation's timeline along the bottom of the 3D viewport."))
        self.show_timeline.setChecked(
            QSettings().value(SHOW_TIMELINE_KEY, False, bool))
        self.show_timeline.toggled.connect(self.toggle_timeline)
        sec.body_layout.addWidget(self.show_timeline)
        layout.addWidget(sec)

    def _build_scenes(self, layout):
        sec = _section(tr("Scenes"), "scenes")
        self.scenes = QListWidget()
        self.scenes.setMinimumHeight(120)
        self.scenes.setMaximumHeight(190)
        self.scenes.setSelectionMode(QListWidget.SingleSelection)
        self.scenes.setToolTip(tr(
            "Ticked scenes are the animation's keyframes, in this order. "
            "Double-click a scene to show it."))
        self.scenes.itemChanged.connect(self._scenes_changed)
        self.scenes.itemSelectionChanged.connect(self._scene_selection_changed)
        self.scenes.itemDoubleClicked.connect(self._show_selected_scene)
        sec.body_layout.addWidget(self.scenes)

        buttons = QHBoxLayout()
        buttons2 = QHBoxLayout()
        self.create_scene_button = QPushButton(tr("New scene"))
        self.delete_scene_button = QPushButton(tr("Delete scene"))
        self.capture_scene_button = QPushButton(tr("Capture current state"))
        self.capture_scene_button.setToolTip(tr(
            "Update the selected scene with the current view, and record the "
            "active section plane's position for the animation."))
        self.create_scene_button.clicked.connect(self._create_scene)
        self.delete_scene_button.clicked.connect(self._delete_scene)
        self.capture_scene_button.clicked.connect(self._capture_selected_scene)
        # Two rows: the tab must fit the side tray's narrow floor (the
        # widest tab sets the width of all of them).
        buttons.addWidget(self.create_scene_button)
        buttons.addWidget(self.delete_scene_button)
        buttons2.addWidget(self.capture_scene_button)
        sec.body_layout.addLayout(buttons)
        sec.body_layout.addLayout(buttons2)

        self.activate_section_button = QPushButton(tr("Activate selected section plane"))
        self.activate_section_button.setToolTip(tr(
            "Select a section plane in the viewport and make it the active "
            "cut here."))
        self.activate_section_button.clicked.connect(self._activate_selected_section_plane)
        sec.body_layout.addWidget(self.activate_section_button)
        # The button's answer, right under it (it went to the Render
        # section's status line, out of sight: the button looked dead).
        self.section_feedback = QLabel()
        self.section_feedback.setWordWrap(True)
        self.section_feedback.setVisible(False)
        sec.body_layout.addWidget(self.section_feedback)

        hint = QLabel(tr(
            "To move a section: show a scene → activate its plane → move the "
            "plane → Capture current state. Two consecutive scenes on the "
            "same plane interpolate it."))
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px;")
        sec.body_layout.addWidget(hint)

        self.scene_state_label = QLabel()
        self.scene_state_label.setWordWrap(True)
        self.scene_state_label.setFrameShape(QFrame.StyledPanel)
        self.scene_state_label.setMinimumHeight(70)
        sec.body_layout.addWidget(self.scene_state_label)

        self.scene_states_table = QTableWidget(0, 6)
        self.scene_states_table.setHorizontalHeaderLabels([
            tr("Scene"), tr("Captured"), tr("Plane"), "UID", tr("Point"),
            tr("Normal vector")])
        self.scene_states_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.scene_states_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.scene_states_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.scene_states_table.setMinimumHeight(110)
        self.scene_states_table.setMaximumHeight(200)
        self.scene_states_table.horizontalHeader().setStretchLastSection(True)
        self.scene_states_table.itemSelectionChanged.connect(self._table_scene_selected)
        sec.body_layout.addWidget(self.scene_states_table)

        self.interpolate_section_checkbox = QCheckBox(
            tr("Interpolate the section plane"))
        self.interpolate_section_checkbox.setToolTip(tr(
            "Two consecutive scenes whose cut is the same section plane move "
            "it smoothly from one position to the other."))
        self.interpolate_section_checkbox.setChecked(True)
        self.interpolate_section_checkbox.toggled.connect(self._interpolation_toggled)
        sec.body_layout.addWidget(self.interpolate_section_checkbox)
        layout.addWidget(sec)

    def _build_settings(self, layout):
        sec = _section(tr("Settings"), "settings")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.resolution_x = QSpinBox()
        self.resolution_x.setRange(16, 16384)
        self.resolution_x.setValue(1920)
        self.resolution_x.setSuffix(" px")
        self.resolution_y = QSpinBox()
        self.resolution_y.setRange(16, 16384)
        self.resolution_y.setValue(1080)
        self.resolution_y.setSuffix(" px")
        # One per line: side by side they did not fit the narrow tray.
        form.addRow(tr("Width:"), self.resolution_x)
        form.addRow(tr("Height:"), self.resolution_y)

        self.fps_combo = QComboBox()
        for value in FPS_PRESETS:
            label = (str(value).rstrip("0").rstrip(".")
                     if isinstance(value, float) else str(value))
            self.fps_combo.addItem(label, value)
        self.fps_combo.addItem(tr("Custom"), "custom")
        self.fps_combo.setCurrentIndex(FPS_PRESETS.index(24))
        self.fps_custom = QDoubleSpinBox()
        self.fps_custom.setRange(0.001, 1000.0)
        self.fps_custom.setDecimals(3)
        self.fps_custom.setValue(24.0)
        self.fps_custom.setSuffix(" fps")
        self.fps_custom.setVisible(False)
        form.addRow(tr("Frame rate:"), _row(self.fps_combo, self.fps_custom))

        self.duration = QSpinBox()
        self.duration.setRange(1, 36000)
        self.duration.setValue(10)
        self.duration.setSuffix(" s")
        form.addRow(tr("Duration:"), self.duration)
        self.total_frames_label = QLabel()
        self.total_frames_label.setStyleSheet("font-size: 11px;")
        form.addRow("", self.total_frames_label)

        self.codec_combo = QComboBox()
        self.codec_combo.addItems(list(CODECS))
        self.crf = QSpinBox()
        self.crf.setRange(0, 51)
        self.crf.setValue(20)
        self.crf.setToolTip(tr("Lower is better quality and a bigger file; 18–23 is usual."))
        form.addRow(tr("Codec:"), self.codec_combo)
        form.addRow(tr("Quality (CRF):"), self.crf)
        sec.body_layout.addLayout(form)

        for w in (self.resolution_x, self.resolution_y, self.duration):
            w.valueChanged.connect(self._timeline_settings_changed)
        self.fps_combo.currentIndexChanged.connect(self._fps_mode_changed)
        self.fps_custom.valueChanged.connect(self._timeline_settings_changed)
        self.crf.valueChanged.connect(self._changed)
        self.codec_combo.currentIndexChanged.connect(self._changed)
        layout.addWidget(sec)

    def _build_output(self, layout):
        sec = _section(tr("Output"), "output")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.file_type = QComboBox()
        self.file_type.addItem(tr("Image sequence"), OUT_IMAGES)
        self.file_type.addItem(tr("Video"), OUT_VIDEO)
        self.file_type.addItem(tr("Image sequence + video"), OUT_BOTH)
        self.file_type.currentIndexChanged.connect(self._output_changed)
        form.addRow(tr("Output:"), self.file_type)

        self.igz_path = QLineEdit()
        self.igz_path.setPlaceholderText(tr("Where the .igz is (the output goes beside it)"))
        self.igz_path.setReadOnly(True)
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.setToolTip(tr("Browse…"))
        browse.clicked.connect(self.choose_igz_file)
        form.addRow(tr("Document:"), _row(self.igz_path, browse))

        self.output_folder_name = QLineEdit("Secuencia")
        self.output_folder_name.editingFinished.connect(self._output_changed)
        form.addRow(tr("Folder name:"), self.output_folder_name)
        self.image_prefix = QLineEdit("img_")
        self.image_prefix.editingFinished.connect(self._output_changed)
        form.addRow(tr("Image prefix:"), self.image_prefix)
        self.video_name = QLineEdit()
        self.video_name.editingFinished.connect(self._video_name_changed)
        form.addRow(tr("Video name:"), self.video_name)
        self.output_info = QLabel()
        self.output_info.setWordWrap(True)
        self.output_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.output_info.setStyleSheet("font-size: 11px;")
        form.addRow("", self.output_info)
        sec.body_layout.addLayout(form)
        layout.addWidget(sec)

    def _build_ffmpeg(self, layout):
        sec = _section("FFmpeg", "ffmpeg")
        self.ffmpeg_label = QLabel()
        self.ffmpeg_label.setWordWrap(True)
        self.ffmpeg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sec.body_layout.addWidget(self.ffmpeg_label)
        choose = QPushButton(tr("Choose ffmpeg…"))
        choose.setToolTip(tr("Use an ffmpeg executable you already have."))
        choose.clicked.connect(self._choose_ffmpeg)
        download = QPushButton(tr("Download…"))
        download.setToolTip(tr("Download FFmpeg (free software) into your user folder."))
        download.clicked.connect(self.ensure_ffmpeg)
        row = QHBoxLayout()
        row.addWidget(choose)
        row.addWidget(download)
        sec.body_layout.addLayout(row)
        layout.addWidget(sec)

    def _build_render(self, layout):
        sec = _section(tr("Render"), "render")
        self.export_button = QPushButton(tr("Render animation"))
        self.export_button.clicked.connect(self.export)
        self.cancel_button = QPushButton(tr("Cancel"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_render)
        row = QHBoxLayout()
        row.addWidget(self.export_button, 1)
        row.addWidget(self.cancel_button)
        sec.body_layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        sec.body_layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        sec.body_layout.addWidget(self.progress)
        self.show_framebuffer_button = QPushButton(tr("Show the frame buffer"))
        self.show_framebuffer_button.setToolTip(tr(
            "The window the frames appear in as they are rendered."))
        self.show_framebuffer_button.clicked.connect(self.show_framebuffer)
        sec.body_layout.addWidget(self.show_framebuffer_button)
        layout.addWidget(sec)

    def _build_console(self, layout):
        sec = _section(tr("Console"), "console", default_open=False)
        self.console_section = sec
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        copy = QPushButton(tr("Copy"))
        copy.setToolTip(tr("Copy the selected text, or all of it."))
        copy.clicked.connect(self._console_copy)
        clear = QPushButton(tr("Clear"))
        clear.clicked.connect(lambda: self.console_output.clear())
        bar.addWidget(copy)
        bar.addWidget(clear)
        bar.addStretch(1)
        sec.body_layout.addLayout(bar)
        self.console_output = QPlainTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.console_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.console_output.setMinimumHeight(150)
        self.console_output.setMaximumBlockCount(2000)
        self.console_output.setPlaceholderText(tr(
            "What the render does — its settings, Blender's output and any "
            "error — appears here."))
        self._apply_console_colors()
        sec.body_layout.addWidget(self.console_output, 1)
        layout.addWidget(sec)

    def _apply_console_colors(self):
        pal = QApplication.palette()
        self.console_output.setStyleSheet(
            "QPlainTextEdit { "
            f"background-color: {pal.color(QPalette.Base).name()}; "
            f"color: {pal.color(QPalette.Text).name()}; "
            f"border: 1px solid {pal.color(QPalette.Mid).name()}; padding: 4px; }}")

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.PaletteChange and hasattr(self, "console_output"):
            self._apply_console_colors()

    def console_log(self, message):
        """Append to the Console (opening it, so a render is seen)."""
        text = str(message).rstrip("\n")
        if not text:
            return
        if not self.console_section.header.isChecked():
            self.console_section.set_open(True)
        cursor = self.console_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.console_output.setTextCursor(cursor)
        self.console_output.insertPlainText(text + "\n")
        self.console_output.ensureCursorVisible()

    def _console_copy(self):
        if self.console_output.textCursor().hasSelection():
            self.console_output.copy()
        else:
            QApplication.clipboard().setText(self.console_output.toPlainText())

    def show_framebuffer(self):
        if self.framebuffer is None:
            self.framebuffer = AnimationFrameBuffer(self.window())
        self.framebuffer.show()
        self.framebuffer.raise_()
        self.framebuffer.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        # The Render tab's quality may have changed meanwhile.
        self._update_native_render_info()

    # ---- Timeline ------------------------------------------------------------
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize and hasattr(self, "_fit_timeline_width"):
            self._fit_timeline_width()
        return super().eventFilter(watched, event)

    def set_dock(self, dock):
        self.dock = dock

    def set_timeline(self, timeline, dock):
        self.timeline = timeline
        self.timeline_dock = dock
        self.timeline.frameChanged.connect(self.preview_frame)
        self.timeline_dock.setVisible(self.show_timeline.isChecked())
        self._update_timeline()

    def toggle_timeline(self, visible):
        QSettings().setValue(SHOW_TIMELINE_KEY, bool(visible))
        if self.timeline_dock is not None:
            self.timeline_dock.setVisible(bool(visible))
            if visible and hasattr(self, "_fit_timeline_width"):
                self._fit_timeline_width()

    def _update_timeline(self):
        frames = max(1, int(round(self.duration.value() * self._fps_value())))
        self.total_frames_label.setText(tr("{n} frames", n=frames))
        if self.timeline is not None:
            self.timeline.set_keys(self.selected_views(), self.duration.value(),
                                   self._fps_value())

    def preview_frame(self, frame):
        if self._frame_preview_busy:
            return
        views = self.selected_views()
        if not views or self.timeline is None:
            return
        self._frame_preview_busy = True
        try:
            apply_frame(self.app.viewport, views, frame, self.timeline.total_frames)
        except Exception as exc:  # noqa: BLE001 — a preview never breaks the tab
            self.status.setText(tr("Preview: {error}", error=exc))
        finally:
            self._frame_preview_busy = False

    # ---- The document --------------------------------------------------------
    def _raw_document(self) -> dict:
        data = self.app.document_data({})
        return data if isinstance(data, dict) else {}

    def _poll(self, *_args):
        """Catch up with the document — cheaply: New / Open (another scene
        object), a scene added, deleted or moved elsewhere, or the stored
        data changed under the tab (an undo of one of its settings)."""
        if self._loading:
            return
        scene = getattr(self.app, "scene", None)
        if scene is not self._scene_obj:
            self._scene_obj = scene
            self._views_snapshot = []
            self._load_document_data()
            self.refresh_scenes()
            self._refresh_output_defaults(force=True)
            return
        if not self._save_timer.isActive():
            raw = self._raw_document()
            text = json.dumps(raw, sort_keys=True, default=str)
            if text != self._stored_json:
                self._load_document_data()
                self.refresh_scenes()
                return
        views = list(getattr(scene, "saved_views", None) or [])
        if ([id(v) for v in views] != [id(v) for v in self._views_snapshot]
                or [v.name for v in views] != getattr(self, "_names_snapshot", [])):
            self.refresh_scenes()

    def _load_document_data(self):
        self._loading = True
        try:
            document = self._raw_document()
            self._stored_json = json.dumps(document, sort_keys=True, default=str)
            plugin, _migrated = get_plugin_data(document)
            self._animation_data = plugin
            s = plugin.get("settings", {})
            mode = int(s.get("image_type", s.get("renderer_mode", 0)) or 0)
            self.image_type.setCurrentIndex(1 if mode == RENDER_BLENDER else 0)
            engine = str(s.get("blender_engine", "cycles")).lower()
            self.blender_engine.setCurrentIndex(1 if engine == "eevee" else 0)
            processing = str(s.get("processing", PROCESSING_NATIVE)).lower()
            self.processing.setCurrentIndex(1 if processing == PROCESSING_CUSTOM else 0)
            self.resolution_x.setValue(int(s.get("resolution_x", 1920)))
            self.resolution_y.setValue(int(s.get("resolution_y", 1080)))
            self.duration.setValue(int(s.get("duration", 10)))
            self.crf.setValue(int(s.get("crf", 20)))
            codec = str(s.get("codec", "H.264"))
            self.codec_combo.setCurrentIndex(1 if codec == "H.265 / HEVC" else 0)
            if bool(s.get("fps_custom_mode", False)):
                self.fps_custom.setValue(float(s.get("fps_custom", 24.0)))
                self.fps_combo.setCurrentIndex(self.fps_combo.count() - 1)
            else:
                fps = float(s.get("fps", 24))
                idx = next((i for i, v in enumerate(FPS_PRESETS)
                            if abs(float(v) - fps) < 1e-9), FPS_PRESETS.index(24))
                self.fps_combo.setCurrentIndex(idx)
            self.fps_custom.setVisible(self.fps_combo.currentData() == "custom")
            self.file_type.setCurrentIndex(max(0, min(2, int(s.get("file_type", 0)))))
            self.image_prefix.setText(str(s.get("image_prefix", "img_")))
            self.output_folder_name.setText(
                str(s.get("output_folder_name", "Secuencia") or "Secuencia"))
            stored_igz = str(s.get("igz_path", "")).strip()
            self._igz_path_edited = bool(stored_igz)
            self.igz_path.setText(stored_igz or self._document_path_string() or "")
            video = str(s.get("video_name", "")).strip()
            self._video_name_edited = bool(video)
            self.video_name.setText(video or self._default_video_name())
            self.noise_threshold_checkbox.setChecked(bool(s.get("noise_threshold_enabled", True)))
            self.noise_threshold.setText(str(s.get("noise_threshold", "0.0100")))
            self.max_samples.setText(str(s.get("max_samples", 128)))
            self.min_samples.setText(str(s.get("min_samples", 0)))
            self.time_limit.setText(str(s.get("time_limit", 0)))
            self.samples.setText(str(s.get("samples", 128)))
            sel = plugin.get("scene_selection", {})
            self._selection_saved = bool(sel.get("saved", False))
            self._selected_indices = set(sel.get("indices", []))
            self._interpolate_section = bool(plugin.get("interpolate_section", True))
            self.interpolate_section_checkbox.setChecked(self._interpolate_section)
        except (TypeError, ValueError):
            self._animation_data = default_plugin_data()
            self._selection_saved = False
            self._selected_indices = set()
            self._interpolate_section = True
        finally:
            self._loading = False
        self._update_renderer_controls()
        self._load_blender_path()

    def _changed(self, *_args):
        """A setting changed: store it — once the user pauses, so a spin of
        the resolution arrows is ONE undo step, not twenty."""
        if self._loading:
            return
        self._update_output_hint()
        self._save_timer.start()

    def _plugin_payload(self) -> dict:
        plugin = normalize_plugin_data(self._animation_data)
        plugin["version"] = SCHEMA_VERSION
        plugin["settings"] = {
            "image_type": int(self.image_type.currentData() or 0),
            "renderer_mode": int(self.image_type.currentData() or 0),
            "blender_engine": str(self.blender_engine.currentData() or "cycles"),
            "processing": str(self.processing.currentData() or PROCESSING_NATIVE),
            "resolution_x": int(self.resolution_x.value()),
            "resolution_y": int(self.resolution_y.value()),
            "fps": float(self._fps_value()),
            "fps_custom_mode": self.fps_combo.currentData() == "custom",
            "fps_custom": float(self.fps_custom.value()),
            "crf": int(self.crf.value()),
            "codec": self.codec_combo.currentText(),
            "duration": int(self.duration.value()),
            "file_type": int(self.file_type.currentData() or 0),
            "image_prefix": self._safe_prefix(),
            "output_folder_name": self._safe_folder_name(),
            "igz_path": self.igz_path.text().strip() if self._igz_path_edited else "",
            "video_name": self.video_name.text().strip() if self._video_name_edited else "",
            "noise_threshold_enabled": bool(self.noise_threshold_checkbox.isChecked()),
            "noise_threshold": self._float_field(self.noise_threshold, 0.01),
            "max_samples": self._int_field(self.max_samples, 128),
            "min_samples": self._int_field(self.min_samples, 0),
            "time_limit": self._float_field(self.time_limit, 0.0),
            "samples": self._int_field(self.samples, 128),
        }
        plugin["scene_selection"] = {
            "saved": True,
            "indices": [i for i in range(self.scenes.count())
                        if self.scenes.item(i).checkState() == Qt.Checked]}
        plugin["interpolate_section"] = bool(self._interpolate_section)
        plugin["scenes"] = list(self._animation_data.get("scenes", []))
        return plugin

    def _flush_save(self):
        """Write the tab's data into the document — only when it differs
        from what is stored (each write is an undo step and makes the
        document unsaved). The data is this extension's slot itself (as in
        v0.22.66); an older nested or loose layout is rewritten so on the
        first real change, never just by opening the file."""
        if self._loading:
            return
        document = self._plugin_payload()
        text = json.dumps(document, sort_keys=True, default=str)
        if text == self._stored_json:
            return
        try:
            self.app.set_document_data(document)
            self._stored_json = json.dumps(self._raw_document(), sort_keys=True,
                                           default=str)
        except Exception as exc:  # noqa: BLE001 — the tab says it, nothing breaks
            self.status.setText(tr("The animation settings could not be saved: {error}",
                                   error=exc))

    def _save_now(self):
        self._save_timer.stop()
        self._flush_save()

    # ---- Paths and names -----------------------------------------------------
    def _document_path(self) -> Path | None:
        window = getattr(self.app, "window", None)
        value = getattr(window, "_current_path", None)
        if not value:
            return None
        try:
            p = Path(str(value)).expanduser()
            return p.resolve() if p.suffix.lower() == ".igz" else None
        except (TypeError, ValueError, OSError):
            return None

    def _document_path_string(self):
        path = self._document_path()
        return str(path) if path is not None else None

    def _current_igz_base_path(self) -> Path | None:
        raw = self.igz_path.text().strip()
        if raw:
            try:
                p = Path(raw).expanduser()
                if p.suffix.lower() == ".igz":
                    return p
            except (TypeError, ValueError, OSError):
                pass
        return self._document_path()

    def _safe_folder_name(self) -> str:
        name = (self.output_folder_name.text().strip() or "Secuencia")
        name = name.replace("/", "_").replace("\\", "_").rstrip(" .")
        return name if name and name not in {".", ".."} else "Secuencia"

    def _default_output_dir(self) -> Path:
        p = self._current_igz_base_path()
        base = p.parent if p else Path.home()
        return base / self._safe_folder_name()

    def _default_video_name(self) -> str:
        p = self._document_path()
        return f"{p.stem}_animacion.mp4" if p else "animacion.mp4"

    def _safe_video_name(self):
        name = (self.video_name.text().strip() or self._default_video_name())
        name = name.replace("/", "_").replace("\\", "_")
        return name if name.lower().endswith(".mp4") else name + ".mp4"

    def _safe_prefix(self):
        prefix = self.image_prefix.text() or "img_"
        return prefix.replace("/", "_").replace("\\", "_")

    def _refresh_output_defaults(self, force=False):
        if force or not self._igz_path_edited:
            if not self._igz_path_edited:
                self.igz_path.setText(self._document_path_string() or "")
        if not self.output_folder_name.text().strip():
            self.output_folder_name.setText("Secuencia")
        if not self._video_name_edited:
            self.video_name.setText(self._default_video_name())
        self._update_output_hint()

    def _video_name_changed(self):
        self._video_name_edited = bool(self.video_name.text().strip())
        self._changed()

    def _output_changed(self, *_args):
        self._changed()

    def _update_output_hint(self, *_args):
        base = self._default_output_dir()
        images = str(base / (self._safe_prefix() + "000001.png")) + " …"
        video = str(base / self._safe_video_name())
        kind = self.file_type.currentData()
        lines = []
        if kind in (OUT_IMAGES, OUT_BOTH):
            lines.append(tr("Images: {path}", path=images))
        if kind in (OUT_VIDEO, OUT_BOTH):
            lines.append(tr("Video: {path}", path=video))
        self.output_info.setText(breakable("\n".join(lines)))
        self.output_info.setToolTip("\n".join(lines))
        self._update_render_status()

    def choose_igz_file(self):
        current = self.igz_path.text().strip() or self._document_path_string() or str(Path.home())
        p, _ = QFileDialog.getOpenFileName(
            self, tr("Choose the .igz file"), current,
            tr("IngeTrazo (*.igz);;All files (*)"))
        if p:
            self.igz_path.setText(str(Path(p).resolve()))
            self._igz_path_edited = True
            self._changed()

    # ---- Renderer ------------------------------------------------------------
    def _renderer_changed(self, *_args):
        self._update_renderer_controls()
        self._changed()

    def _renderer_picked(self, *_args):
        if (self.image_type.currentData() == RENDER_BLENDER
                and self._blender_is_available() is None):
            self._prompt_blender_install()

    def _update_renderer_controls(self):
        is_blender = self.image_type.currentData() == RENDER_BLENDER
        self.blender_box.setVisible(is_blender)
        self._update_sampling_controls()
        self._update_render_status()

    def _update_sampling_controls(self):
        """Native shows what the Render tab's quality gives; Custom shows
        only the controls the engine has (EEVEE: samples)."""
        custom = self.processing.currentData() == PROCESSING_CUSTOM
        engine = str(self.blender_engine.currentData() or ENGINE_CYCLES)
        vis = visible_custom_controls(engine, self.noise_threshold_checkbox.isChecked())
        for w in self.native_rows:
            w.setVisible(not custom)
        self.noise_threshold_row.setVisible(custom and vis["noise_threshold"])
        self.noise_threshold.setEnabled(self.noise_threshold_checkbox.isChecked())
        self.max_samples_row.setVisible(custom and vis["adaptive_samples"])
        self.min_samples_row.setVisible(custom and vis["adaptive_samples"])
        self.samples_row.setVisible(custom and vis["fixed_samples"])
        self.time_limit_row.setVisible(custom and vis["time_limit"])
        self._update_native_render_info()

    def _native_quality(self) -> int:
        from .blender_renderer import native_quality
        return native_quality()

    def _update_native_render_info(self):
        if not hasattr(self, "native_quality"):
            return
        q = self._native_quality()
        engine = str(self.blender_engine.currentData() or ENGINE_CYCLES)
        self.native_quality.setText(tr(QUALITY_NAMES[q]))
        self.native_samples.setText(str(native_samples(engine, q)))

    def _blender_settings_changed(self, *_args):
        self._update_sampling_controls()
        self._changed()

    def _sampling_mode_changed(self, *_args):
        self._update_sampling_controls()
        self._changed()

    def _load_blender_path(self):
        try:
            from core.render_blender import SETTINGS_KEY, find_blender
            saved = str(QSettings().value(SETTINGS_KEY, "") or "").strip()
            found = find_blender(saved or None)
            path = saved if saved and Path(saved).is_file() else (found.where if found else "")
        except Exception:  # noqa: BLE001 — no Blender, an empty field
            path = ""
        self.blender_path.setText(path)
        self.blender_path.setToolTip(path or tr("Blender is not set up."))

    def _choose_blender_executable(self):
        start = self.blender_path.text().strip() or str(Path.home())
        flt = (tr("Blender (blender.exe);;All files (*)") if os.name == "nt"
               else tr("All files (*)"))
        selected, _ = QFileDialog.getOpenFileName(
            self, tr("Choose the Blender executable"), start, flt)
        if not selected:
            return
        selected = str(Path(selected).resolve())
        from core.render_blender import SETTINGS_KEY
        QSettings().setValue(SETTINGS_KEY, selected)
        self._load_blender_path()
        self._update_render_status()

    def _blender_is_available(self):
        try:
            from core.render_blender import SETTINGS_KEY, find_blender
            saved = (self.blender_path.text().strip()
                     or str(QSettings().value(SETTINGS_KEY, "") or "").strip())
            return find_blender(saved or None)
        except Exception:  # noqa: BLE001
            return None

    def _prompt_blender_install(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Blender not found"))
        box.setText(tr("Blender is not installed, or its executable was not found."))
        box.setInformativeText(tr(
            "Blender is free software. Install it from its official site, "
            "then choose its executable here. Nothing is installed without "
            "you."))
        download = box.addButton(tr("Open the official download"), QMessageBox.AcceptRole)
        choose = box.addButton(tr("Choose the executable"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is download:
            webbrowser.open("https://www.blender.org/download/")
        elif box.clickedButton() is choose:
            self._choose_blender_executable()

    def _blender_sampling_settings(self):
        """The panel's sampling, normalized (``processing`` included)."""
        return normalize_sampling(
            str(self.blender_engine.currentData() or ENGINE_CYCLES),
            str(self.processing.currentData() or PROCESSING_NATIVE),
            {"noise_threshold_enabled": self.noise_threshold_checkbox.isChecked(),
             "noise_threshold": self._float_field(self.noise_threshold, 0.01),
             "max_samples": self._int_field(self.max_samples, 128),
             "min_samples": self._int_field(self.min_samples, 0),
             "samples": self._int_field(self.samples, 128),
             "time_limit": self._float_field(self.time_limit, 0.0)})

    def _sampling_text(self) -> str:
        """The sampling one line long, for the Console and the frame buffer."""
        cfg = self._blender_sampling_settings()
        q = self._native_quality()
        fields = request_fields(cfg["engine"], cfg["processing"], cfg, q)
        text = describe(cfg["engine"], fields)
        if cfg["processing"] == PROCESSING_NATIVE:
            text = tr("Native · {quality}", quality=tr(QUALITY_NAMES[q])) + " · " + text
        return text

    @staticmethod
    def _float_field(field, default):
        try:
            return max(0.0, float(str(field.text()).strip().replace(",", ".")))
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _int_field(field, default):
        try:
            return max(0, int(float(str(field.text()).strip())))
        except (TypeError, ValueError):
            return int(default)

    def _update_render_status(self, *_args):
        count = len(self.selected_views()) if hasattr(self, "scenes") else 0
        renderer = self.image_type.currentText()
        if self.image_type.currentData() == RENDER_BLENDER:
            renderer += " · " + self.blender_engine.currentText()
            renderer += " · " + self.processing.currentText()
        self.status.setText(tr(
            "{n} scene(s) ticked to animate.\nRenderer: {renderer}\nOutput: {output}",
            n=count, renderer=renderer, output=self.file_type.currentText()))

    # ---- Scenes --------------------------------------------------------------
    def _remap_records(self, views):
        """Keep each scene's animation record with ITS scene when the list
        changed outside the tab (a scene deleted or moved in Properties).
        Records are stored by position — that is what documents hold — and
        followed here by the scene objects of this session."""
        old = list(self._views_snapshot)
        if not old:
            return
        records = {int(r.get("index", -1)): r
                   for r in self._animation_data.get("scenes", [])
                   if isinstance(r, dict)}
        old_checked = {old[i] for i in range(len(old))
                       if i in self._selected_indices} if self._selection_saved else None
        moved, checked = [], set()
        for j, v in enumerate(views):
            try:
                i = next(k for k, o in enumerate(old) if o is v)
            except StopIteration:
                i = None
            rec = records.get(i) if i is not None else None
            if rec is not None:
                rec = dict(rec)
                rec["index"] = j
                moved.append(rec)
            if old_checked is None or i is None or v in old_checked:
                checked.add(j)
        self._animation_data["scenes"] = moved
        self._selected_indices = checked
        self._selection_saved = True

    def refresh_scenes(self):
        """Rebuild the scene list from the document — never touching the
        viewport."""
        scene = self.app.scene
        views = list(getattr(scene, "saved_views", None) or [])
        if self._views_snapshot and [id(v) for v in views] != [id(v) for v in self._views_snapshot]:
            self._remap_records(views)
        previous_row = self.scenes.currentRow()
        self.scenes.blockSignals(True)
        self.scenes.clear()
        for n, view in enumerate(views, 1):
            self._attach_animation_state(view, n - 1)
            item = QListWidgetItem(f"{n:02d}  {view.name}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            checked = (not self._selection_saved) or ((n - 1) in self._selected_indices)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            item.setData(Qt.UserRole, view)
            item.setToolTip(view.name)
            item.setSizeHint(QSize(260, 26))
            self.scenes.addItem(item)
        self.scenes.blockSignals(False)
        self._views_snapshot = views
        self._names_snapshot = [v.name for v in views]
        self._reconcile_records(views)
        if self.scenes.count():
            row = previous_row if 0 <= previous_row < self.scenes.count() else 0
            self.scenes.setCurrentRow(row)
        self._scene_selection_changed()
        self._update_timeline()
        self._update_render_status()

    def _reconcile_records(self, views):
        by_index = {}
        for record in self._animation_data.get("scenes", []):
            try:
                idx = int(record.get("index", -1))
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= idx < len(views) and idx not in by_index:
                by_index[idx] = record
        for idx, view in enumerate(views):
            rec = by_index.setdefault(idx, {"id": new_scene_id(), "index": idx,
                                            "name": view.name, "captured": False,
                                            "section": None})
            rec["index"] = idx
            rec["name"] = view.name
        self._animation_data["scenes"] = [by_index[i] for i in sorted(by_index)]

    def _scene_record(self, index, view=None, create=True):
        if view is None and 0 <= index < self.scenes.count():
            view = self.scenes.item(index).data(Qt.UserRole)
        if view is None:
            return None
        records = self._animation_data.setdefault("scenes", [])
        for record in records:
            try:
                if int(record.get("index", -1)) == index:
                    record["name"] = view.name
                    return record
            except (TypeError, ValueError, AttributeError):
                continue
        if not create:
            return None
        record = {"id": new_scene_id(), "index": index, "name": view.name,
                  "captured": False, "section": None}
        records.append(record)
        return record

    def _attach_animation_state(self, view, index):
        record = self._scene_record(index, view) or {}
        view._igz_anim_scene_index = index
        view._igz_anim_scene_id = record.get("id")
        view._igz_anim_section_state = record.get("section") if record.get("captured") else None
        view._igz_anim_interpolate_section = bool(self._interpolate_section)

    def selected_views(self):
        views = []
        for i in range(self.scenes.count()):
            item = self.scenes.item(i)
            if item.checkState() == Qt.Checked:
                view = item.data(Qt.UserRole)
                self._attach_animation_state(view, i)
                views.append(view)
        return views

    def _scenes_changed(self, *_args):
        self._selected_indices = {i for i in range(self.scenes.count())
                                  if self.scenes.item(i).checkState() == Qt.Checked}
        self._selection_saved = True
        self._update_timeline()
        self._update_render_status()
        self._changed()

    def _scene_selection_changed(self):
        row = self.scenes.currentRow()
        if row < 0 or row >= self.scenes.count():
            self.scene_state_label.setText(tr("Select a scene."))
            self._refresh_scene_states_table()
            return
        view = self.scenes.item(row).data(Qt.UserRole)
        record = self._scene_record(row, view) or {}
        section = record.get("section") if record.get("captured") else None
        if not record.get("captured"):
            text = tr("Scene: {name}\nSection plane: not captured.", name=view.name)
        elif not section or not section.get("active"):
            text = tr("Scene: {name}\nSection plane: no active cut.", name=view.name)
        else:
            text = tr("Scene: {name}\nPlane: {symbol} (UID {uid})\nPoint: {point}\n"
                      "Normal: {normal}", name=view.name,
                      symbol=section.get("symbol") or "—",
                      uid=section.get("uid", "—"),
                      point=self._format_vector(section.get("point")),
                      normal=self._format_vector(section.get("normal")))
        self.scene_state_label.setText(text)
        self._refresh_scene_states_table()
        self._select_table_row(row)

    def _refresh_scene_states_table(self):
        table = self.scene_states_table
        table.blockSignals(True)
        table.setRowCount(self.scenes.count())
        for row in range(self.scenes.count()):
            view = self.scenes.item(row).data(Qt.UserRole)
            record = self._scene_record(row, view) or {}
            section = record.get("section") if record.get("captured") else None
            active = bool(section and section.get("active"))
            values = [view.name,
                      tr("Yes") if record.get("captured") else "—",
                      str(section.get("symbol") or "—") if active else "—",
                      str(section.get("uid", "—")) if active else "—",
                      self._format_vector(section.get("point")) if active else "—",
                      self._format_vector(section.get("normal")) if active else "—"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, col, item)
        table.resizeColumnsToContents()
        table.blockSignals(False)

    @staticmethod
    def _format_vector(values):
        if not values or len(values) != 3:
            return "—"
        return f"({float(values[0]):.3f}, {float(values[1]):.3f}, {float(values[2]):.3f})"

    def _select_table_row(self, row):
        table = self.scene_states_table
        table.blockSignals(True)
        table.clearSelection()
        if 0 <= row < table.rowCount():
            table.selectRow(row)
        table.blockSignals(False)

    def _table_scene_selected(self):
        rows = self.scene_states_table.selectionModel().selectedRows()
        if rows and 0 <= rows[0].row() < self.scenes.count():
            self.scenes.setCurrentRow(rows[0].row())

    def _touch_scenes(self):
        """The model changed its scenes: the Scenes panel, the viewport's
        scene label and the window's «unsaved» mark follow, as they do for
        the Scenes panel's own buttons."""
        scene = self.app.scene
        scene.version += 1
        window = getattr(self.app, "window", None)
        tray = getattr(window, "tray", None)
        for panel in ("scenes", "layers"):
            p = getattr(tray, panel, None)
            if p is not None:
                p.refresh()
        self.app.viewport.update()

    def _next_scene_name(self) -> str:
        existing = {str(v.name).strip().casefold()
                    for v in getattr(self.app.scene, "saved_views", None) or []}
        base = tr("Scene")
        n = 1
        while f"{base} {n}".casefold() in existing:
            n += 1
        return f"{base} {n}"

    def _create_scene(self):
        """A new scene from the current view — like + Scene in Properties,
        with a name asked first. It becomes the active scene."""
        from core.saved_views import SavedView
        default_name = self._next_scene_name()
        name, accepted = QInputDialog.getText(
            self, tr("New scene"), tr("Scene name:"), text=default_name)
        if not accepted:
            return
        name = name.strip() or default_name
        scene = self.app.scene
        if any(v.name.casefold() == name.casefold() for v in scene.saved_views):
            QMessageBox.warning(self, tr("New scene"),
                                tr("A scene named '{name}' already exists", name=name))
            return
        view = SavedView.capture(name, scene, self.app.viewport.camera)
        scene.saved_views.append(view)
        scene.active_view = view
        self._selected_indices.add(len(scene.saved_views) - 1)
        self._touch_scenes()
        self.refresh_scenes()
        self.scenes.setCurrentRow(self.scenes.count() - 1)
        self._changed()
        self.status.setText(tr("Scene created: {name}", name=name))

    def _delete_scene(self):
        row = self.scenes.currentRow()
        scene = self.app.scene
        views = scene.saved_views
        if row < 0 or row >= len(views):
            QMessageBox.information(self, tr("Animation"), tr("Select a scene to delete."))
            return
        view = views[row]
        answer = QMessageBox.question(
            self, tr("Delete scene"),
            tr("Delete the scene '{name}'? Its animation state goes with it.",
               name=view.name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        del views[row]
        if getattr(scene, "active_view", None) is view:
            scene.active_view = None
        self._touch_scenes()
        self.refresh_scenes()           # the records follow their scenes
        self._changed()
        self.status.setText(tr("Scene deleted: {name}", name=view.name))

    def _show_selected_scene(self, item=None):
        """Double-click: show the scene exactly as Properties ▸ Scenes does
        (the same native recall), and it becomes the active scene."""
        if item is None:
            item = self.scenes.currentItem()
        view = item.data(Qt.UserRole) if item is not None else None
        if view is None:
            return
        scene = self.app.scene
        view.apply(scene, self.app.viewport.camera)
        scene.active_view = view
        window = getattr(self.app, "window", None)
        sync = getattr(window, "_sync_style_menu", None)
        if callable(sync):
            sync()
        self._touch_scenes()
        self.status.setText(tr("Scene shown: {name}", name=view.name))

    def _activate_selected_section_plane(self):
        from core.history import SetActiveSectionCommand
        from core.section import SectionPlane
        planes = [e for e in self.app.scene.selection if isinstance(e, SectionPlane)]
        if not planes:
            self._section_feedback(tr(
                "Select a section plane in the viewport first (click its "
                "border with Select), then press the button again."), ok=False)
            return
        plane = planes[0]
        # Section planes have a symbol (A, B…) and usually no name.
        label = (getattr(plane, "name", "") or getattr(plane, "symbol", "")
                 or "—")
        was_active = bool(getattr(plane, "active", False))
        if not was_active:
            self.app.viewport.history.execute(SetActiveSectionCommand(plane))
        self.app.scene.show_section_planes = True
        self.app.scene.show_section_cuts = True
        self.app.scene.bump_view()
        self.app.viewport.update()
        text = tr("Plane {name} (UID {uid}) is already the active cut.",
                  name=label, uid=getattr(plane, "uid", "—")) if was_active else tr(
            "Active section plane: {name} (UID {uid}). Move it, then capture "
            "the scene.", name=label, uid=getattr(plane, "uid", "—"))
        self._section_feedback(text, ok=True)

    def _section_feedback(self, text: str, ok: bool) -> None:
        color = "#16a34a" if ok else "#d97706"
        self.section_feedback.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.section_feedback.setText(text)
        self.section_feedback.setVisible(True)
        self.status.setText(text)
        self.console_log(text)

    def _capture_selected_scene(self):
        row = self.scenes.currentRow()
        if row < 0:
            QMessageBox.information(self, tr("Animation"), tr("Select a scene."))
            return
        view = self.scenes.item(row).data(Qt.UserRole)
        if view is None:
            return
        scene = self.app.scene
        view.recapture(scene, self.app.viewport.camera)
        scene.active_view = view
        section = capture_section_state(self.app.viewport)
        record = self._scene_record(row, view)
        record["captured"] = True
        record["index"] = row
        record["name"] = view.name
        record["section"] = section
        self._attach_animation_state(view, row)
        self._touch_scenes()
        self._scene_selection_changed()
        self._update_timeline()
        self._changed()
        if section.get("active"):
            self.status.setText(tr("{name} captured, with the cut UID {uid}.",
                                   name=view.name, uid=section.get("uid")))
        else:
            self.status.setText(tr("{name} captured, without a section cut.",
                                   name=view.name))

    def _interpolation_toggled(self, checked):
        self._interpolate_section = bool(checked)
        for i in range(self.scenes.count()):
            self._attach_animation_state(self.scenes.item(i).data(Qt.UserRole), i)
        self._update_timeline()
        self._changed()

    # ---- Settings ------------------------------------------------------------
    def _fps_mode_changed(self, *_args):
        self.fps_custom.setVisible(self.fps_combo.currentData() == "custom")
        self._timeline_settings_changed()

    def _fps_value(self) -> float:
        value = self.fps_combo.currentData()
        return float(self.fps_custom.value()) if value == "custom" else float(value)

    def _timeline_settings_changed(self, *_args):
        self._update_timeline()
        self._changed()

    # ---- FFmpeg ----------------------------------------------------------------
    def refresh_ffmpeg_status(self):
        p = self.ffmpeg_manager.find()
        self.ffmpeg_label.setToolTip(str(p or ""))
        self.ffmpeg_label.setText(
            tr("Found: {path}", path=breakable(p)) if p else
            tr("Not found — needed for video only (an image sequence works "
               "without it)."))

    def _choose_ffmpeg(self):
        flt = (tr("FFmpeg (ffmpeg.exe);;All files (*)") if os.name == "nt"
               else tr("All files (*)"))
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Choose ffmpeg"), str(Path.home()), flt)
        if path:
            remember_ffmpeg(str(Path(path).resolve()))
            self.refresh_ffmpeg_status()

    def ensure_ffmpeg(self):
        path = self.ffmpeg_manager.find()
        if path:
            self.refresh_ffmpeg_status()
            return path
        answer = QMessageBox.question(
            self, tr("FFmpeg needed"),
            tr("FFmpeg (free software) turns the frames into a video and is "
               "not installed. Download it into your user folder?\n\n{path}",
               path=self.ffmpeg_manager.install_location()),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return None
        progress = QProgressDialog(tr("Downloading FFmpeg…"), tr("Cancel"), 0, 100, self)
        progress.setWindowTitle("FFmpeg")
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        cancelled = False

        def report(value, message):
            nonlocal cancelled
            progress.setLabelText(message)
            progress.setValue(max(0, min(100, int(value))))
            QApplication.processEvents()
            if progress.wasCanceled():
                cancelled = True
                raise RuntimeError(tr("Download cancelled."))

        progress.show()
        try:
            path = self.ffmpeg_manager.download_and_install(progress=report)
            progress.close()
            self.refresh_ffmpeg_status()
            QMessageBox.information(self, "FFmpeg",
                                    tr("FFmpeg installed:\n{path}", path=path))
            return path
        except Exception as exc:  # noqa: BLE001 — the box says what went wrong
            progress.close()
            if not cancelled:
                QMessageBox.critical(self, tr("FFmpeg could not be installed"), str(exc))
            return None

    # ---- Render ----------------------------------------------------------------
    def _cancel_render(self):
        if self.framebuffer is not None:
            self.framebuffer.request_cancel()
        elif self._renderer is not None:
            self._renderer.cancelled = True
        self.status.setText(tr("Cancelling…"))

    def export(self):
        views = self.selected_views()
        if len(views) < 2:
            QMessageBox.warning(self, tr("Animation"), tr("Tick at least two scenes."))
            return
        kind = self.file_type.currentData()
        want_images = kind in (OUT_IMAGES, OUT_BOTH)
        want_video = kind in (OUT_VIDEO, OUT_BOTH)
        outdir = self._default_output_dir()
        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, tr("Animation"),
                                 tr("The output folder could not be made:\n{error}", error=exc))
            return
        ffmpeg = None
        if want_video:
            ffmpeg = self.ffmpeg_manager.find() or self.ensure_ffmpeg()
            if not ffmpeg:
                self.status.setText(tr("No video without FFmpeg — choose an image sequence, "
                                       "or choose or download ffmpeg."))
                return
        blender = self.image_type.currentData() == RENDER_BLENDER
        if blender and self._blender_is_available() is None:
            self._prompt_blender_install()
            if self._blender_is_available() is None:
                self.status.setText(tr("Blender is not available."))
                return
        self._save_now()
        size = (self.resolution_x.value(), self.resolution_y.value())
        fps = self._fps_value()
        renderer_text = self.image_type.currentText()
        if blender:
            renderer_text += " · " + self.blender_engine.currentText()
        sampling_text = self._sampling_text() if blender else "—"
        outputs = []
        if want_images:
            outputs.append(str(outdir / (self._safe_prefix() + "000001.png")) + " …")
        if want_video:
            outputs.append(str(outdir / self._safe_video_name()))
        self.console_log(tr("=== Render started ==="))
        self.console_log(tr("Scenes: {n} · Output: {output} · Renderer: {renderer}",
                            n=len(views), output=self.file_type.currentText(),
                            renderer=renderer_text))
        self.console_log(tr("Resolution: {w} × {h} px · {fps:g} fps",
                            w=size[0], h=size[1], fps=fps))
        if blender:
            self.console_log(tr("Sampling: {what}", what=sampling_text))

        self.show_framebuffer()
        fb = self.framebuffer
        frames = None
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.export_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

        def frame_callback(path, done, total):
            fb.add_frame(path, done, total, frame_segment_text(views, done, total))

        try:
            if blender:
                from .blender_renderer import BlenderAnimationRender
                self._renderer = BlenderAnimationRender(
                    self.app, views, self.duration.value(), fps, size,
                    engine=str(self.blender_engine.currentData() or "cycles"),
                    blender_path=self.blender_path.text().strip(),
                    frame_callback=frame_callback,
                    output_dir=outdir if want_images else None,
                    image_prefix=self._safe_prefix(),
                    sampling=self._blender_sampling_settings(),
                    log_callback=lambda m: self.console_log("[Blender] " + str(m)),
                    control=fb, quality=self._native_quality())
                cancel_hook = self._renderer.request_cancel
            else:
                self._renderer = AnimationRender(
                    self.app.viewport, views, self.duration.value(), fps, size,
                    output_dir=outdir if want_images else None,
                    image_prefix=self._safe_prefix(),
                    frame_callback=frame_callback, control=fb)
                cancel_hook = None
            fb.start(self._renderer.total_frames, {
                "renderer": renderer_text, "sampling": sampling_text,
                "resolution": f"{size[0]} × {size[1]} px",
                "fps": f"{fps:g} fps", "output": "\n".join(outputs)},
                cancel_hook=cancel_hook)

            def progress(done, total, message):
                self.progress.setValue(int(done * 100 / max(1, total)))
                self.status.setText(message)
                self.console_log(f"[{done}/{total}] {message}")

            frames = self._renderer.render(progress)
            done = []
            if want_images:
                done.append(tr("Images: {path}", path=outdir))
            if want_video:
                video_path = outdir / self._safe_video_name()
                self.status.setText(tr("Encoding the video…"))
                self.console_log(tr("Encoding the video…"))
                QApplication.processEvents()
                FFmpegExporter(ffmpeg).export_png_sequence(
                    frames, fps, video_path, self.crf.value(),
                    codec=self.codec_combo.currentText())
                done.append(tr("Video: {path}", path=video_path))
            self.progress.setValue(100)
            fb.finish(DONE)
            self.status.setText(tr("Animation rendered.") + "\n" + "\n".join(done))
            for line in done:
                self.console_log(line)
            self.console_log(tr("=== Render finished ==="))
        except RenderCancelled:
            fb.finish(CANCELLED)
            self.status.setText(tr("Render cancelled."))
            self.console_log(tr("=== Render cancelled ==="))
        except Exception as exc:  # noqa: BLE001 — the box says what went wrong
            fb.finish(FAILED)
            self.status.setText(tr("The render failed."))
            self.console_log(tr("[ERROR] The render failed: {error}", error=exc))
            QMessageBox.critical(self, tr("Animation"), str(exc))
        finally:
            temp = getattr(self._renderer, "temp_dir", None)
            if temp is not None:
                shutil.rmtree(temp, ignore_errors=True)
            fb.forget_missing()
            self._renderer = None
            self.progress.setVisible(False)
            self.export_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
