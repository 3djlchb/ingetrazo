# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""The section fill picker: a «Select colour» window in three tabs.

* **Colours** — flat colours (a palette of our own, a hex field and the
  system colour chooser): what any renderer paints exactly.
* **Colours / Material** — the Colours category of Properties ▸ Material
  (RAL Classic), by name.
* **Textures / Material** — the rest of the material library (brick,
  concrete, stone, wood…): the cut is filled with the texture at its real
  size; its average colour is the fallback a flat renderer uses.

``pick_fill(parent, current)`` returns ``{"rgb", "name", "texture", "size"}``
or ``None`` (cancelled). ``texture`` is stored relative to resources/textures
(``core.style.stored_texture_path``) so a document travels between machines.
"""
from __future__ import annotations

import colorsys
import json

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap, QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from core.i18n import current_language, tr
from views.color_dialog import get_color

SWATCH = 22
THUMB = 48
#: The library's category ids → their names (Properties ▸ Material's).
CATEGORY_NAMES = {
    "brick": "Brick", "concrete": "Concrete", "stone": "Stone",
    "wood": "Wood", "roof": "Roofing", "floor": "Flooring",
    "metal": "Metal", "ground": "Ground", "glass": "Glass",
    "water": "Water", "wall": "Wall", "wallpaper": "Wallpaper",
    "fabric": "Fabric", "rug": "Rug", "sky": "Sky", "misc": "Miscellaneous",
}


def _textures_dir():
    from core.paths import app_root
    return app_root() / "resources" / "textures"


def flat_palette() -> list:
    """Our own palette, in rows: 12 hues from dark to light, then greys —
    plain HSV steps, every one an exact sRGB colour."""
    rows = []
    for value, sat in ((0.35, 0.9), (0.55, 0.9), (0.75, 0.85), (0.92, 0.75),
                       (1.0, 0.5), (1.0, 0.25)):
        rows.append([colorsys.hsv_to_rgb(h / 12.0, sat, value) for h in range(12)])
    rows.append([(g / 11.0,) * 3 for g in range(12)])
    return rows


def hex_of(rgb) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c * 255))) for c in rgb[:3])


def rgb_of_hex(text: str):
    c = QColor(text)
    return (c.redF(), c.greenF(), c.blueF()) if c.isValid() else None


def ral_colors() -> list:
    """``[(code, name, rgb)]`` of RAL Classic, as Properties ▸ Material."""
    from core.paths import app_root
    path = app_root() / "resources" / "colors" / "ral.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    spanish = current_language().startswith("es")
    out = []
    for fam in data.get("families", []):
        for c in fam.get("colors", []):
            name = c.get("name_es") if spanish and c.get("name_es") else c.get("name", "")
            out.append((c.get("code", ""), name, tuple(c["rgb"])))
    return out


def texture_categories() -> list:
    """``[(id, title, items)]`` of the material library, colours excluded
    (they are the other tab)."""
    manifest = _textures_dir() / "library.json"
    if not manifest.is_file():
        return []
    data = json.loads(manifest.read_text(encoding="utf-8"))
    out = []
    for cat in data.get("categories", []):
        items = cat.get("items", [])
        if items:
            out.append((cat["id"], tr(CATEGORY_NAMES.get(cat["id"], cat["id"])), items))
    return out


def average_rgb(path) -> tuple:
    """A texture's average colour — the flat fallback of a textured fill."""
    img = QImage(str(path))
    if img.isNull():
        return (0.35, 0.37, 0.41)
    c = QColor(img.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).pixel(0, 0))
    return (c.redF(), c.greenF(), c.blueF())


def fill_pixmap(fill: dict, w=THUMB, h=THUMB) -> QPixmap:
    """The swatch of a fill: its texture or its colour."""
    from core.style import texture_path
    tex = texture_path((fill or {}).get("texture", ""))
    if tex is not None:
        img = QImage(str(tex))
        if not img.isNull():
            return QPixmap.fromImage(img.scaled(w, h, Qt.IgnoreAspectRatio,
                                                Qt.SmoothTransformation))
    pm = QPixmap(w, h)
    pm.fill(QColor.fromRgbF(*((fill or {}).get("rgb") or (0.35, 0.37, 0.41))[:3]))
    return pm


class FillPickerDialog(QDialog):
    def __init__(self, parent=None, current: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Section fill colour"))
        self.resize(560, 520)
        cur = dict(current or {})
        self._fill = {"rgb": tuple(cur.get("rgb") or (0.35, 0.37, 0.41)),
                      "name": cur.get("name") or "", "texture": cur.get("texture") or "",
                      "size": tuple(cur.get("size") or (1.0, 1.0))}
        if not self._fill["name"]:
            self._fill["name"] = hex_of(self._fill["rgb"])

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_colors(), tr("Colors"))
        self.tabs.addTab(self._build_ral(), tr("Colors / Material"))
        self.tabs.addTab(self._build_textures(), tr("Textures / Material"))
        root.addWidget(self.tabs, 1)

        foot = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedSize(72, 44)
        self.preview.setFrameShape(QFrame.StyledPanel)
        self.preview_name = QLabel()
        self.preview_name.setWordWrap(True)
        foot.addWidget(QLabel(tr("Fill:")))
        foot.addWidget(self.preview)
        foot.addWidget(self.preview_name, 1)
        root.addLayout(foot)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.tabs.setCurrentIndex(2 if self._fill["texture"] else 0)
        self._show()

    # ---- tab 1: flat colours ----------------------------------------------------
    def _build_colors(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(tr("Flat colours, the same in the viewport and in a render.")))
        grid = QGridLayout()
        grid.setSpacing(2)
        self.palette_buttons = []
        for r, row in enumerate(flat_palette()):
            for c, rgb in enumerate(row):
                b = QToolButton()
                b.setFixedSize(SWATCH, SWATCH)
                b.setAutoRaise(False)
                b.setStyleSheet(f"QToolButton {{ background: {hex_of(rgb)}; border: 1px solid #555; }}"
                                "QToolButton:hover { border: 2px solid #fff; }")
                b.setToolTip(hex_of(rgb))
                b.clicked.connect(lambda _=False, col=rgb: self.choose_color(col))
                grid.addWidget(b, r, c)
                self.palette_buttons.append(b)
        lay.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Hex:")))
        self.hex_edit = QLineEdit()
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setPlaceholderText("#RRGGBB")
        self.hex_edit.setValidator(QRegularExpressionValidator(
            QRegularExpression("#?[0-9A-Fa-f]{0,6}"), self.hex_edit))
        self.hex_edit.editingFinished.connect(self._hex_typed)
        row.addWidget(self.hex_edit)
        more = QPushButton(tr("More colours…"))
        more.clicked.connect(self._more_colors)
        row.addWidget(more)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return page

    def _hex_typed(self):
        text = self.hex_edit.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        rgb = rgb_of_hex(text) if len(text) == 7 else None
        if rgb is not None:
            self.choose_color(rgb)

    def _more_colors(self):
        c = get_color(QColor.fromRgbF(*self._fill["rgb"]), self,
                      tr("More colours…"))
        if c.isValid():
            self.choose_color((c.redF(), c.greenF(), c.blueF()))

    def choose_color(self, rgb, name: str | None = None):
        rgb = tuple(float(x) for x in rgb[:3])
        self._fill = {"rgb": rgb, "name": name or hex_of(rgb), "texture": "",
                      "size": (1.0, 1.0)}
        self._show()

    # ---- tab 2: Colours / Material (RAL) ----------------------------------------
    def _build_ral(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.ral_filter = QLineEdit()
        self.ral_filter.setPlaceholderText(tr("Search: code or name"))
        self.ral_filter.textChanged.connect(self._filter_ral)
        lay.addWidget(self.ral_filter)
        self.ral_list = QListWidget()
        self.ral_list.setViewMode(QListWidget.IconMode)
        self.ral_list.setIconSize(QSize(THUMB - 12, THUMB - 12))
        self.ral_list.setGridSize(QSize(84, 76))
        self.ral_list.setResizeMode(QListWidget.Adjust)
        self.ral_list.setWordWrap(True)
        self.ral_list.setMovement(QListWidget.Static)
        for code, name, rgb in ral_colors():
            pm = QPixmap(THUMB - 12, THUMB - 12)
            pm.fill(QColor.fromRgbF(*rgb))
            item = QListWidgetItem(QIcon(pm), code)
            item.setToolTip(f"{code} · {name}")
            item.setData(Qt.UserRole, (rgb, f"{code} {name}"))
            self.ral_list.addItem(item)
        self.ral_list.itemClicked.connect(
            lambda it: self.choose_color(*it.data(Qt.UserRole)))
        lay.addWidget(self.ral_list, 1)
        return page

    def _filter_ral(self, text):
        text = text.strip().lower()
        for i in range(self.ral_list.count()):
            it = self.ral_list.item(i)
            it.setHidden(bool(text) and text not in it.toolTip().lower())

    # ---- tab 3: Textures / Material ----------------------------------------------
    def _build_textures(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.category = QComboBox()
        self._categories = texture_categories()
        for cid, title, items in self._categories:
            self.category.addItem(f"{title}  ({len(items)})", cid)
        self.category.currentIndexChanged.connect(self._fill_category)
        lay.addWidget(self.category)
        self.texture_list = QListWidget()
        self.texture_list.setViewMode(QListWidget.IconMode)
        self.texture_list.setIconSize(QSize(THUMB, THUMB))
        self.texture_list.setGridSize(QSize(90, 84))
        self.texture_list.setResizeMode(QListWidget.Adjust)
        self.texture_list.setWordWrap(True)
        self.texture_list.setMovement(QListWidget.Static)
        self.texture_list.itemClicked.connect(self._texture_clicked)
        lay.addWidget(self.texture_list, 1)
        if self._categories:
            self._fill_category(0)
        return page

    def _fill_category(self, index):
        """Thumbnails are read when their category is shown (the library has
        hundreds of images)."""
        self.texture_list.clear()
        if not (0 <= index < len(self._categories)):
            return
        base = _textures_dir() / "library"
        for item in self._categories[index][2]:
            path = base / item["file"]
            img = QImage(str(path))
            if img.isNull():
                continue
            icon = QIcon(QPixmap.fromImage(img.scaled(THUMB, THUMB, Qt.IgnoreAspectRatio,
                                                      Qt.SmoothTransformation)))
            name = tr(item.get("name", path.stem))
            it = QListWidgetItem(icon, name)
            it.setToolTip(f"{name} · {float(item.get('sw', 1.0)):g} × "
                          f"{float(item.get('sh', 1.0)):g} m")
            it.setData(Qt.UserRole, (str(path), name,
                                     (float(item.get("sw", 1.0) or 1.0),
                                      float(item.get("sh", 1.0) or 1.0))))
            self.texture_list.addItem(it)

    def _texture_clicked(self, item):
        path, name, size = item.data(Qt.UserRole)
        self.choose_texture(path, name, size)

    def choose_texture(self, path, name, size=(1.0, 1.0)):
        from core.style import stored_texture_path
        self._fill = {"rgb": average_rgb(path), "name": name,
                      "texture": stored_texture_path(path),
                      "size": (float(size[0]), float(size[1]))}
        self._show()

    # ---- result --------------------------------------------------------------------
    def _show(self):
        self.preview.setPixmap(fill_pixmap(self._fill, 72, 44))
        kind = tr("Texture") if self._fill["texture"] else hex_of(self._fill["rgb"])
        self.preview_name.setText(f"{self._fill['name']}\n{kind}")
        if hasattr(self, "hex_edit") and not self._fill["texture"]:
            self.hex_edit.setText(hex_of(self._fill["rgb"]))

    def result_fill(self) -> dict:
        f = dict(self._fill)
        f["rgb"] = [float(x) for x in f["rgb"]]
        f["size"] = [float(x) for x in f["size"]]
        return f


def pick_fill(parent, current: dict | None = None):
    dlg = FillPickerDialog(parent, current)
    return dlg.result_fill() if dlg.exec() == QDialog.Accepted else None
