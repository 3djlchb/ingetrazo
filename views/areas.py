# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Areas: the window's centre split into editors, the way Blender does it.

An opt-in layout (Window ▸ Layout ▸ Areas); the classic one — the 3D
viewport alone in the centre — stays the default and is untouched.

* An **area** has a slim header: top left the **editor type** it shows
  (3D Viewport, Python Console, Terrain profile…), top right its menu
  (split, join, maximise, close). Borders between areas are dragged.
* **Editors** come from a registry: the core registers its own here; an
  extension adds its own with ``ExtensionApp.add_editor``. An area whose
  editor is not available (its extension is missing) shows as *Empty* and
  keeps waiting for it, so a saved layout is never lost.
* A **single** editor (the 3D viewport, a borrowed panel) is in one area
  at most: picking it elsewhere moves it there, and the area it leaves
  takes the other's editor (a swap).
* The layout — the splits, their sizes, each area's editor — is a user
  preference (QSettings), never part of a document.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QSplitter, QTabBar,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from core.i18n import tr

#: Window ▸ Layout: the classic window, or areas.
MODE_KEY = "ui/layout_mode"
LAYOUT_KEY = "ui/areas_layout"
MODE_CLASSIC, MODE_AREAS = "classic", "areas"
EMPTY = "empty"
VIEWPORT = "viewport3d"
PROPERTIES = "properties"
#: The categories of the editor menu, in order (Blender's, more or less).
CATEGORIES = ("General", "Animation", "Data", "Scripting")
#: What «Areas» starts with the first time: the 3D viewport on top, the
#: Python console under it, and on the right the side tray as the
#: Properties area — where it always is.
DEFAULT_LAYOUT = {"split": "h", "sizes": [1000, 440],
                  "children": [{"split": "v", "sizes": [700, 220],
                                "children": [{"editor": VIEWPORT},
                                             {"editor": "python_console"}]},
                               {"editor": PROPERTIES}]}


@dataclass
class EditorType:
    id: str
    title: str                       # English; shown through tr()
    create: Callable                 # create(window, area) -> QWidget
    category: str = "General"
    #: Give back a widget lent by someone else (the viewport, a dock's
    #: content) instead of deleting it: release(window, widget).
    release: Optional[Callable] = None
    single: bool = False
    order: int = 100


def editor_registry(window) -> dict:
    reg = getattr(window, "_editor_types", None)
    if reg is None:
        reg = window._editor_types = {}
    return reg


def register_editor(window, editor: EditorType) -> None:
    """Add (or replace) an editor type; areas waiting for it fill in."""
    editor_registry(window)[editor.id] = editor
    manager = getattr(window, "area_manager", None)
    if manager is not None:
        manager.editor_registered(editor.id)


class EmptyEditor(QWidget):
    """An area with no editor yet — or whose editor is not available."""

    def __init__(self, wanted: str = "", parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("color: palette(mid);")
        lay.addWidget(self.label, 1)
        self.label.setText(
            tr("Editor not available: {name}\nIts extension is not loaded; "
               "the area keeps waiting for it.", name=wanted)
            if wanted and wanted != EMPTY else
            tr("Choose an editor at the top left."))


class Area(QWidget):
    """One area: header (editor type ▾ … ⋯) and the editor under it."""

    def __init__(self, manager: "AreaManager", editor_id: str = EMPTY) -> None:
        super().__init__()
        self.manager = manager
        #: What this area shows, or wants to show once it is registered.
        self.editor_id = editor_id
        self.content: Optional[QWidget] = None
        self._wrap: Optional[QScrollArea] = None
        self.setObjectName("area")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = QFrame()
        self.header.setObjectName("area_header")
        self.header.setFixedHeight(26)
        self.header.setStyleSheet(
            "#area_header { background: palette(window);"
            " border-bottom: 1px solid palette(mid); }")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(4, 1, 4, 1)
        hl.setSpacing(4)
        self.editor_button = QToolButton()
        self.editor_button.setPopupMode(QToolButton.InstantPopup)
        self.editor_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.editor_button.setToolTip(tr("Editor type"))
        self.editor_button.setStyleSheet(
            "QToolButton { font-weight: 600; padding: 1px 6px; }"
            "QToolButton::menu-indicator { image: none; width: 0; }")
        self.editor_menu = QMenu(self.editor_button)
        self.editor_menu.aboutToShow.connect(self._fill_editor_menu)
        self.editor_button.setMenu(self.editor_menu)
        hl.addWidget(self.editor_button)
        hl.addStretch(1)
        self.area_button = QToolButton()
        self.area_button.setText("⋯")
        self.area_button.setToolTip(tr("Area options"))
        self.area_button.setPopupMode(QToolButton.InstantPopup)
        self.area_button.setAutoRaise(True)
        self.area_button.setStyleSheet(
            "QToolButton::menu-indicator { image: none; width: 0; }")
        self.area_menu = QMenu(self.area_button)
        self.area_menu.aboutToShow.connect(self._fill_area_menu)
        self.area_button.setMenu(self.area_menu)
        hl.addWidget(self.area_button)
        self.header.setContextMenuPolicy(Qt.CustomContextMenu)
        self.header.customContextMenuRequested.connect(
            lambda pos: self._area_menu_at(self.header.mapToGlobal(pos)))
        root.addWidget(self.header)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.body, 1)

    # ---- the editor shown ------------------------------------------------------
    def set_content(self, editor_id: str, widget: QWidget) -> None:
        """Show ``widget``. An editor goes in a scroll area of its own: an
        area may be made smaller than the editor would like (the console's
        buttons, the tray's 500 px) and it scrolls instead of forcing the
        whole layout wider — as Blender's areas clip. The viewport draws
        to any size and goes in directly."""
        self.editor_id = editor_id
        self.content = widget
        if editor_id == VIEWPORT:
            self.body.addWidget(widget, 1)
        else:
            wrap = QScrollArea()
            wrap.setWidgetResizable(True)
            wrap.setFrameShape(QFrame.NoFrame)
            wrap.setWidget(widget)
            self._wrap = wrap
            self.body.addWidget(wrap, 1)
        widget.show()
        self._update_title()

    def take_content(self) -> Optional[QWidget]:
        w, self.content = self.content, None
        if self._wrap is not None:
            wrap, self._wrap = self._wrap, None
            if w is not None:
                wrap.takeWidget()
            self.body.removeWidget(wrap)
            wrap.setParent(None)
            wrap.deleteLater()
        elif w is not None:
            self.body.removeWidget(w)
        return w

    def _update_title(self) -> None:
        ed = editor_registry(self.manager.window).get(self.editor_id)
        name = tr(ed.title) if ed is not None and self.editor_id != EMPTY else tr("Empty")
        self.editor_button.setText(f"{name}  ▾")

    # ---- menus -----------------------------------------------------------------
    def _fill_editor_menu(self) -> None:
        menu = self.editor_menu
        menu.clear()
        reg = editor_registry(self.manager.window)
        groups: dict = {}
        for ed in reg.values():
            if ed.id == EMPTY:
                continue
            groups.setdefault(ed.category, []).append(ed)
        cats = [c for c in CATEGORIES if c in groups] + sorted(
            c for c in groups if c not in CATEGORIES)
        for cat in cats:
            menu.addSection(tr(cat))
            for ed in sorted(groups[cat], key=lambda e: (e.order, tr(e.title))):
                act = menu.addAction(tr(ed.title))
                act.setCheckable(True)
                act.setChecked(ed.id == self.editor_id)
                act.triggered.connect(
                    lambda _=False, i=ed.id: self.manager.set_editor(self, i))
        menu.addSeparator()
        act = menu.addAction(tr("Empty"))
        act.triggered.connect(lambda: self.manager.set_editor(self, EMPTY))

    def _fill_area_menu(self) -> None:
        self._populate_area_menu(self.area_menu)

    def _area_menu_at(self, global_pos) -> None:
        menu = QMenu(self)
        self._populate_area_menu(menu)
        menu.exec(global_pos)

    def _populate_area_menu(self, menu: QMenu) -> None:
        m = self.manager
        menu.clear()
        menu.addAction(tr("Split vertically"),
                       lambda: m.split(self, Qt.Horizontal))
        menu.addAction(tr("Split horizontally"),
                       lambda: m.split(self, Qt.Vertical))
        menu.addSeparator()
        join = menu.addAction(tr("Join with the neighbouring area"),
                              lambda: m.join(self))
        join.setEnabled(m.can_join(self))
        maxi = menu.addAction(
            tr("Back to the areas") if m.maximized is self else tr("Maximize area"),
            lambda: m.toggle_maximize(self))
        maxi.setShortcut("Ctrl+Space")
        menu.addSeparator()
        close = menu.addAction(tr("Close area"), lambda: m.join(self))
        close.setEnabled(m.can_join(self))


class AreaManager(QWidget):
    """The window's centre in «Areas» mode: a tree of splitters and areas."""

    layoutChanged = Signal()

    def __init__(self, window, data: Optional[dict] = None) -> None:
        super().__init__()
        self.window = window
        self.setObjectName("area_manager")
        self.maximized: Optional[Area] = None
        self._hidden_by_max: list = []
        self._root_lay = QVBoxLayout(self)
        self._root_lay.setContentsMargins(0, 0, 0, 0)
        self._root: Optional[QWidget] = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.save)
        self.layoutChanged.connect(self._save_timer.start)
        self.layoutChanged.connect(self._place_viewport)
        if data is not None:
            self.build(data)

    # The centre takes what the window leaves it, as the lone viewport does
    # in the classic layout (no size of its own to ask for): asking for its
    # children's (the viewport's 640 px) made the window hand the side tray
    # all the rest — 715 px instead of 439.
    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize()                      # no preference, as the viewport

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(240, 160)

    # ---- building / saving ---------------------------------------------------------
    def build(self, data: dict) -> None:
        self._root = self._make(data)
        self._root_lay.addWidget(self._root)
        self._ensure_viewport()

    def _make(self, data) -> QWidget:
        if isinstance(data, dict) and data.get("split") in ("h", "v") \
                and len(data.get("children") or []) >= 2:
            sp = self._splitter(Qt.Horizontal if data["split"] == "h" else Qt.Vertical)
            for child in data["children"]:
                sp.addWidget(self._make(child))
            sizes = data.get("sizes")
            if isinstance(sizes, list) and len(sizes) == sp.count():
                sp.setSizes([max(20, int(s)) for s in sizes])
            return sp
        editor = str((data or {}).get("editor") or EMPTY) if isinstance(data, dict) else EMPTY
        area = Area(self)
        self._fill(area, editor)
        return area

    def _splitter(self, orientation) -> QSplitter:
        sp = QSplitter(orientation)
        sp.setChildrenCollapsible(False)
        sp.setHandleWidth(4)
        sp.splitterMoved.connect(lambda *_: self.layoutChanged.emit())
        sp.splitterMoved.connect(lambda *_: self._place_viewport())
        return sp

    def to_dict(self, w: Optional[QWidget] = None) -> dict:
        w = self._root if w is None else w
        if isinstance(w, QSplitter):
            return {"split": "h" if w.orientation() == Qt.Horizontal else "v",
                    "sizes": list(w.sizes()),
                    "children": [self.to_dict(w.widget(i)) for i in range(w.count())]}
        return {"editor": getattr(w, "editor_id", EMPTY)}

    def save(self) -> None:
        QSettings().setValue(LAYOUT_KEY, json.dumps(self.to_dict()))

    def _place_viewport(self) -> None:
        stage = getattr(self.window, "stage", None)
        if stage is not None:
            stage.place_soon()

    # ---- areas -----------------------------------------------------------------------
    def areas(self) -> list:
        return [a for a in self.findChildren(Area) if a.manager is self]

    def area_with(self, editor_id: str) -> Optional[Area]:
        return next((a for a in self.areas() if a.editor_id == editor_id
                     and a.content is not None), None)

    def _fill(self, area: Area, editor_id: str) -> None:
        """Put ``editor_id`` in ``area`` (or Empty, waiting for it)."""
        ed = editor_registry(self.window).get(editor_id)
        if ed is None or editor_id == EMPTY:
            area.set_content(EMPTY, EmptyEditor(editor_id))
            area.editor_id = editor_id          # keeps waiting for it
            area._update_title()
            return
        area.set_content(editor_id, ed.create(self.window, area))

    def _clear(self, area: Area) -> None:
        w = area.take_content()
        if w is None:
            return
        ed = editor_registry(self.window).get(area.editor_id)
        if ed is not None and ed.release is not None and not isinstance(w, EmptyEditor):
            ed.release(self.window, w)
        else:
            w.setParent(None)
            w.deleteLater()

    def set_editor(self, area: Area, editor_id: str) -> None:
        """Show ``editor_id`` in ``area``. A single editor shown in another
        area moves here, and that area takes this one's editor (a swap)."""
        if editor_id == area.editor_id and area.content is not None \
                and not isinstance(area.content, EmptyEditor):
            return
        ed = editor_registry(self.window).get(editor_id)
        other = self.area_with(editor_id) if ed is not None and ed.single else None
        mine = area.editor_id
        if other is not None and other is not area:
            # A swap: the two areas trade editors.
            self._clear(other)
            self._clear(area)
            self._fill(area, editor_id)
            self._fill(other, mine)
        elif mine == VIEWPORT:
            # The viewport cannot vanish: it moves by being picked elsewhere.
            self._say(tr("The 3D Viewport must stay in an area: pick it in "
                         "another area to move it there."))
            return
        else:
            self._clear(area)
            self._fill(area, editor_id)
        self.layoutChanged.emit()

    def _say(self, text: str) -> None:
        bar = getattr(self.window, "statusBar", None)
        if callable(bar):
            bar().showMessage(text, 5000)

    def editor_registered(self, editor_id: str) -> None:
        """An editor arrived (an extension's ``setup``): the areas waiting
        for it show it now."""
        for a in self.areas():
            if a.editor_id == editor_id and isinstance(a.content, EmptyEditor):
                self._clear(a)
                self._fill(a, editor_id)

    def _ensure_viewport(self) -> None:
        """Exactly one area holds the 3D viewport (a hand-edited or old
        layout without it gets it in its first area)."""
        if self.area_with(VIEWPORT) is not None:
            return
        areas = self.areas()
        if areas:
            self._clear(areas[0])
            self._fill(areas[0], VIEWPORT)

    # ---- split / join / maximise ---------------------------------------------------
    def split(self, area: Area, orientation) -> Area:
        """Cut ``area`` in two along ``orientation``; the new half is Empty."""
        if self.maximized is not None:
            self.toggle_maximize(self.maximized)
        parent = area.parentWidget()
        new = Area(self)
        self._fill(new, EMPTY)
        sp = self._splitter(orientation)
        if isinstance(parent, QSplitter):
            index = parent.indexOf(area)
            sizes = parent.sizes()
            parent.insertWidget(index, sp)
            sp.addWidget(area)
            sp.addWidget(new)
            parent.setSizes(sizes)
        else:                                   # the root
            self._root_lay.removeWidget(area)
            sp.addWidget(area)
            sp.addWidget(new)
            self._root = sp
            self._root_lay.addWidget(sp)
        total = sp.width() if orientation == Qt.Horizontal else sp.height()
        sp.setSizes([max(1, total // 2)] * 2)
        self.layoutChanged.emit()
        return new

    def can_join(self, area: Area) -> bool:
        return len(self.areas()) > 1

    def join(self, area: Area) -> None:
        """Close ``area``: its neighbour takes its room. The viewport is
        never closed — it moves to the neighbour."""
        if not self.can_join(area):
            return
        if self.maximized is not None:
            self.toggle_maximize(self.maximized)
        parent = area.parentWidget()
        if not isinstance(parent, QSplitter):
            return
        if area.editor_id == VIEWPORT:
            neighbour = self._first_area(parent.widget(1 if parent.indexOf(area) == 0 else 0))
            self._clear(neighbour)
            self._clear(area)
            self._fill(neighbour, VIEWPORT)
        else:
            self._clear(area)
        area.setParent(None)
        area.deleteLater()
        if parent.count() == 1:                 # collapse the splitter
            only = parent.widget(0)
            grand = parent.parentWidget()
            if isinstance(grand, QSplitter):
                index = grand.indexOf(parent)
                sizes = grand.sizes()
                grand.insertWidget(index, only)
                grand.setSizes(sizes)
            else:
                self._root_lay.removeWidget(parent)
                self._root = only
                self._root_lay.addWidget(only)
            parent.setParent(None)
            parent.deleteLater()
        self.layoutChanged.emit()

    @staticmethod
    def _first_area(w) -> Area:
        while isinstance(w, QSplitter):
            w = w.widget(0)
        return w

    def toggle_maximize(self, area: Area) -> None:
        """The area alone in the centre, or back to the layout (Ctrl+Space)."""
        if self.maximized is not None:
            for w in self._hidden_by_max:
                w.show()
            self._hidden_by_max = []
            was, self.maximized = self.maximized, None
            if was is area:
                return
        keep = set()
        w = area
        while w is not None and w is not self:
            keep.add(w)
            w = w.parentWidget()
        hidden = []
        for sp in self.findChildren(QSplitter):
            for i in range(sp.count()):
                child = sp.widget(i)
                if child not in keep and child.isVisible():
                    child.hide()
                    hidden.append(child)
        self._hidden_by_max = hidden
        self.maximized = area

    def set_headers_visible(self, on: bool) -> None:
        for a in self.areas():
            a.header.setVisible(on)

    def rebuild(self, data: dict) -> None:
        """Replace the whole layout with ``data`` (Reset the areas): every
        editor given back first, then built anew."""
        self.dismantle()
        old = self._root
        if old is not None:
            self._root_lay.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        self._root = None
        self.build(data)
        self.layoutChanged.emit()

    # ---- leaving «Areas» -------------------------------------------------------------
    def dismantle(self) -> None:
        """Give every borrowed widget back (the viewport first of all)."""
        self._save_timer.stop()
        if self.maximized is not None:
            self.toggle_maximize(self.maximized)
        for a in self.areas():
            self._clear(a)


class SideTabBar(QTabBar):
    """The narrow strip of tabs on the left edge of the panel, as Blender's
    region tabs: each name written down the strip (top to bottom), the
    current one lit, the strip part of the area — not a block beside it."""

    PAD = 8           # across the strip, each side of the text
    GAP = 14          # along it, each end of the text

    def tabSizeHint(self, index):
        from PySide6.QtCore import QSize
        fm = self.fontMetrics()
        return QSize(fm.height() + self.PAD,
                     fm.horizontalAdvance(self.tabText(index)) + 2 * self.GAP)

    def minimumTabSizeHint(self, index):
        return self.tabSizeHint(index)

    def paintEvent(self, _event):
        from PySide6.QtGui import QColor, QPainter, QPalette
        painter = QPainter(self)
        pal = self.palette()
        base = pal.color(QPalette.Window)
        dark = base.lightness() < 128
        painter.fillRect(self.rect(), base.darker(115) if dark else base.darker(104))
        for i in range(self.count()):
            r = self.tabRect(i).adjusted(1, 1, -1, -1)
            current = i == self.currentIndex()
            if current:
                painter.fillRect(r, pal.color(QPalette.Base))
                painter.fillRect(r.x(), r.y(), 2, r.height(), QColor("#3b82f6"))
            elif r.contains(self.mapFromGlobal(self.cursor().pos())):
                painter.fillRect(r, base.lighter(115) if dark else base.darker(110))
            painter.save()
            painter.setPen(pal.color(QPalette.WindowText if current else QPalette.Text))
            # Down the strip, top to bottom: turned a quarter clockwise.
            painter.translate(r.center().x() + 1, r.center().y())
            painter.rotate(90)
            fm = self.fontMetrics()
            text = self.tabText(i)
            w = fm.horizontalAdvance(text)
            painter.drawText(-w // 2, fm.ascent() // 2 - 1, text)
            painter.restore()
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.update()


class _SizeOnce(QObject):
    """Give child ``index`` of ``split`` ``size`` px when the splitter first
    has its real size (not before the window is laid out), then go."""

    def __init__(self, split: QSplitter, index: int, size: int) -> None:
        super().__init__(split)
        self.split, self.index, self.size = split, index, size
        split.installEventFilter(self)
        if split.isVisible() and split.width() > 0:
            self._queued = True
            QTimer.singleShot(0, self._apply)

    def eventFilter(self, obj, event):  # noqa: N802 — Qt override
        # Seen BEFORE the splitter shares its new size out: apply right after.
        if event.type() == QEvent.Resize and not getattr(self, "_queued", False):
            self._queued = True
            QTimer.singleShot(0, self._apply)
        return False

    def _apply(self) -> None:
        sizes = self.split.sizes()
        total = sum(sizes)
        if total <= 0:
            self._queued = False                 # not laid out yet: next resize
            return
        if len(sizes) != 2:
            self._done()
            return
        if total - self.size < 160:
            # Not the window's final size yet (or too small): next resize.
            self._queued = False
            return
        other = 1 - self.index
        sizes[self.index], sizes[other] = self.size, total - self.size
        self.split.setSizes(sizes)
        self._done()

    def _done(self) -> None:
        self.split.removeEventFilter(self)
        self.deleteLater()


class PropertiesEditor(QTabWidget):
    """The side tray as an area (Data ▸ Properties): the SAME panels as the
    tray's tabs — Properties, BIM, Terrain and the extensions' (Render,
    Levels, AI…) — their tabs down the left edge, as Blender's Properties
    editor has them. They are
    lent: back in the classic layout every panel returns to its tab. Window
    ▸ Panels shows and hides tabs here, and an extension's tab added later
    joins in."""

    def __init__(self, window, parent=None) -> None:
        super().__init__(parent)
        self.window = window
        bar = SideTabBar(self)
        bar.setMouseTracking(True)
        bar.setDrawBase(False)
        self.setTabBar(bar)
        self.setTabPosition(QTabWidget.West)     # down the left, as Blender
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self._lent: dict = {}                   # dock → its widget
        from PySide6.QtCore import QSettings as _S
        hidden = window._hidden_tray_names(_S())
        front = next((d for d in window._sidebar_docks()
                      if d.isVisible() and not d.visibleRegion().isEmpty()), None)
        for dock in window._sidebar_docks():
            if dock.objectName() not in hidden:
                self.adopt(dock)
        if front is not None and front in self._lent:
            self.setCurrentWidget(self._lent[front])

    def docks(self) -> list:
        return list(self._lent)

    def fit_panel_width(self, width: int) -> None:
        """Make the panel (not the area: its side tabs come on top) ``width``
        wide: the area is sized ``width`` + the tabs' column once its
        splitter has its real size — then the width is the user's."""
        area = self.parentWidget()
        while area is not None and not isinstance(area, Area):
            area = area.parentWidget()
        split = area.parentWidget() if area is not None else None
        if not isinstance(split, QSplitter) or split.orientation() != Qt.Horizontal:
            return
        target = int(width) + self.tabBar().sizeHint().width()
        # Kept: an event filter's Python side must outlive this call.
        self._sizer = _SizeOnce(split, split.indexOf(area), target)

    def adopt(self, dock) -> None:
        """Take ``dock``'s panel as a tab (and hide the emptied dock)."""
        if dock in self._lent:
            return
        widget = dock.widget()
        dock.hide()
        if widget is None:
            return
        widget.setParent(None)
        self._lent[dock] = widget
        self.addTab(widget, dock.windowTitle())
        widget.show()

    def set_shown(self, dock, shown: bool) -> None:
        """Window ▸ Panels in the areas layout: a tab here, or not."""
        if shown:
            self.adopt(dock)
            if dock in self._lent:
                self.setCurrentWidget(self._lent[dock])
        else:
            self.give_back(dock, show=False)

    def give_back(self, dock, show: bool) -> None:
        widget = self._lent.pop(dock, None)
        if widget is None:
            return
        self.removeTab(self.indexOf(widget))
        dock.setWidget(widget)
        dock.setVisible(show)

    def give_everything_back(self) -> None:
        """Every panel back in its dock — HIDDEN: with areas the side tray
        never shows as a panel of its own; leaving areas shows the docks
        again (``MainWindow.set_layout_mode``)."""
        front = self.currentWidget()
        self.window._tray_front = next(
            (d for d, w in self._lent.items() if w is front), None)
        for dock in list(self._lent):
            self.give_back(dock, show=False)


class Stage(QWidget):
    """The window's centre, in both layouts: the 3D viewport lives here and
    is NEVER reparented. Classic: it fills the stage. Areas: the area
    manager fills it and the viewport is laid over its area's slot.

    Moving the viewport into an area (reparenting a GL widget) tore its GL
    context down and built it again — ~0.4 s and a flash of the whole
    window on every switch. Placing it costs a setGeometry."""

    def __init__(self, viewport) -> None:
        super().__init__()
        self.setObjectName("stage")
        self.viewport = viewport
        viewport.setParent(self)
        self.manager: Optional["AreaManager"] = None
        self.slot: Optional["ViewportSlot"] = None
        self._queued = False

    # classic: the viewport's own size rules; areas: the manager's
    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        if self.manager is not None:
            return QSize(240, 160)
        return self.viewport.minimumSize()

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize()

    def set_manager(self, manager) -> None:
        self.manager = manager
        if manager is not None:
            manager.setParent(self)
            manager.setGeometry(self.rect())
            manager.show()
            self.viewport.setMinimumSize(*AREA_VIEWPORT_MIN)
        else:
            self.slot = None
            self.viewport.setMinimumSize(*CLASSIC_VIEWPORT_MIN)
        self.updateGeometry()
        self.place()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.manager is not None:
            self.manager.setGeometry(self.rect())
        self.place()

    def place(self) -> None:
        """The viewport over its slot (areas) or over everything (classic)."""
        vp = self.viewport
        if self.manager is None:
            vp.setGeometry(self.rect())
            vp.show()
            return
        slot = self.slot
        if slot is None or not slot.isVisible() or slot.width() <= 0:
            vp.hide()
            return
        rect = QRect(slot.mapTo(self, QPoint(0, 0)), slot.size())
        if vp.geometry() != rect:
            vp.setGeometry(rect)
        if vp.isHidden():
            vp.show()
        vp.raise_()

    def place_soon(self) -> None:
        """After the layout has settled (a splitter dragged, an area split)."""
        if not self._queued:
            self._queued = True

            def go():
                self._queued = False
                self.place()
            QTimer.singleShot(0, go)


class ViewportSlot(QWidget):
    """What the 3D Viewport's area holds: room for the viewport, which the
    stage lays over it. It follows every move of the slot and of the areas
    and splitters above it."""

    def __init__(self, stage: Stage, parent=None) -> None:
        super().__init__(parent)
        self.stage = stage
        self._watched: list = []

    def _watch(self) -> None:
        self._unwatch()
        w = self.parentWidget()
        while w is not None and w is not self.stage:
            w.installEventFilter(self)
            self._watched.append(w)
            w = w.parentWidget()

    def _unwatch(self) -> None:
        for w in self._watched:
            try:
                w.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched = []

    def eventFilter(self, obj, event):  # noqa: N802 — Qt override
        if event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show,
                            QEvent.Hide, QEvent.ParentChange):
            self.stage.place_soon()
        return False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._watch()
        self.stage.place()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.stage.place_soon()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.stage.place()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.stage.place()

    def release(self) -> None:
        self._unwatch()
        if self.stage.slot is self:
            self.stage.slot = None
        self.stage.place()


# ---- the core's own editors ------------------------------------------------------------

#: The viewport's minimum in an area, and alone in the centre.
AREA_VIEWPORT_MIN = (160, 120)
CLASSIC_VIEWPORT_MIN = (640, 480)


def _viewport_create(window, area):
    """The viewport's area holds a slot; the stage lays the viewport over
    it (the viewport itself never changes parent)."""
    stage = window.stage
    slot = ViewportSlot(stage, area)
    old = stage.slot
    stage.slot = slot
    if old is not None and old is not slot:
        old._unwatch()
    return slot


def _viewport_release(window, widget):
    widget.release()
    widget.setParent(None)
    widget.deleteLater()


def _profile_create(window, area):
    dock = window.profile_dock
    inner = dock.widget()
    window._profile_dock_was_open = dock.isVisible()
    dock.hide()
    inner.setParent(area)
    return inner


def _profile_release(window, widget):
    dock = window.profile_dock
    dock.setWidget(widget)
    if getattr(window, "_profile_dock_was_open", False):
        dock.show()


def _properties_create(window, area):
    editor = PropertiesEditor(window, area)
    window._properties_editor = editor
    return editor


def _properties_release(window, widget):
    widget.give_everything_back()
    if getattr(window, "_properties_editor", None) is widget:
        window._properties_editor = None
    widget.setParent(None)
    widget.deleteLater()


def register_core_editors(window) -> None:
    register_editor(window, EditorType(
        VIEWPORT, "3D Viewport", _viewport_create, "General",
        release=_viewport_release, single=True, order=0))
    register_editor(window, EditorType(
        PROPERTIES, "Properties", _properties_create, "Data",
        release=_properties_release, single=True, order=10))
    register_editor(window, EditorType(
        "terrain_profile", "Terrain profile", _profile_create, "Data",
        release=_profile_release, single=True, order=50))


def saved_layout() -> Optional[dict]:
    raw = QSettings().value(LAYOUT_KEY)
    if not raw:
        return None
    try:
        data = json.loads(str(raw))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
