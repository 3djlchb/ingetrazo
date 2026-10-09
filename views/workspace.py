# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Workspaces (Window ▸ Workspace): named arrangements of the window, as
Blender's — «Workspace 1», «Workspace 2»… — switched from a drop-down list
or from tabs on the menu bar's line.

A workspace also keeps its LAYOUT (Window ▸ Layout: classic or areas):
picking it switches to that layout — «Normal» may be classic and «Render»
areas — and changing the layout inside a workspace makes it that
workspace's. It keeps an arrangement for each layout —

* **classic**: the toolbars and docks (``QMainWindow.saveState``), which
  side-tray tabs are open and which one is in front;
* **areas**: the splits, their sizes and each area's editor.

Switching workspaces stores the current arrangement in the workspace being
left and puts the chosen one's back, in whatever layout is in use; changing
the layout keeps the workspace. A new workspace starts as a copy of the
current one. Everything is a user preference (QSettings), never part of a
document.
"""
from __future__ import annotations

import base64
import copy
import json
import uuid
from typing import Optional

from PySide6.QtCore import QByteArray, QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
    QTabBar, QToolBar, QWidget,
)

from core.i18n import tr

LIST_KEY = "ui/workspaces"
CURRENT_KEY = "ui/workspace_current"
VIEW_KEY = "ui/workspace_view"
#: Window ▸ Workspace ▸ Display: a drop-down list at the right of the menu
#: bar, or tabs right after the menus.
VIEW_LIST, VIEW_TABS = "list", "tabs"
_NEW = "__new__"
#: A workspaces template (Preferences ▸ Workspaces ▸ Export / Import).
TEMPLATE_SUFFIX = ".igzws"
TEMPLATE_FORMAT = "ingetrazo-workspaces"
TEMPLATE_VERSION = 1


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class WorkspaceManager(QObject):
    """The window's workspaces: what each keeps, switching, the menu and
    the bar."""

    changed = Signal()

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.spaces: list = []
        self.current = 0
        self.bar: Optional[WorkspaceBar] = None
        self._toolbar: Optional[QToolBar] = None
        self._load()

    # ---- storage ---------------------------------------------------------------------
    def _load(self) -> None:
        st = QSettings()
        try:
            data = json.loads(str(st.value(LIST_KEY) or "[]"))
        except ValueError:
            data = []
        self.spaces = [s for s in data if isinstance(s, dict) and s.get("name")]
        if not self.spaces:
            self.spaces = [self._blank(1)]
        try:
            self.current = int(st.value(CURRENT_KEY, 0))
        except (TypeError, ValueError):
            self.current = 0
        self.current = max(0, min(self.current, len(self.spaces) - 1))

    def save(self) -> None:
        st = QSettings()
        st.setValue(LIST_KEY, json.dumps(self.spaces))
        st.setValue(CURRENT_KEY, self.current)

    @staticmethod
    def _blank(n: int) -> dict:
        return {"id": _new_id(), "name": tr("Workspace {n}", n=n),
                "mode": None, "classic": None, "areas": None}

    def mode(self) -> Optional[str]:
        """The current workspace's layout (``None``: not set yet)."""
        m = self.space().get("mode")
        return m if m in ("classic", "areas") else None

    def set_mode(self, mode: str) -> None:
        """The layout changed inside this workspace: it is its layout now."""
        self.space()["mode"] = mode
        self.save()

    def _next_name(self) -> str:
        names = {s["name"] for s in self.spaces}
        n = len(self.spaces) + 1
        while tr("Workspace {n}", n=n) in names:
            n += 1
        return tr("Workspace {n}", n=n)

    # ---- what a workspace keeps ---------------------------------------------------------
    def names(self) -> list:
        return [s["name"] for s in self.spaces]

    def space(self) -> dict:
        return self.spaces[self.current]

    def areas_layout(self) -> Optional[dict]:
        """The current workspace's areas (``None``: not set up yet)."""
        layout = self.space().get("areas")
        return copy.deepcopy(layout) if isinstance(layout, dict) else None

    def capture(self) -> None:
        """Store the window's arrangement in the current workspace, for the
        layout in use."""
        w = self.window
        space = self.space()
        manager = getattr(w, "area_manager", None)
        space["mode"] = "areas" if manager is not None else "classic"
        if manager is not None:
            space["areas"] = manager.to_dict()
            return
        docks = w._sidebar_docks()
        front = next((d for d in docks if d.isVisible()
                      and not d.visibleRegion().isEmpty()), None)
        space["classic"] = {
            "state": base64.b64encode(bytes(w.saveState())).decode("ascii"),
            "trays": [d.objectName() for d in docks if not d.isHidden()],
            "front": front.objectName() if front is not None else None,
        }

    def apply(self) -> None:
        """Put the current workspace's arrangement back, for the layout in
        use. A workspace that has none for it yet keeps what is on screen
        (and takes it as its own)."""
        w = self.window
        space = self.space()
        manager = getattr(w, "area_manager", None)
        w.setUpdatesEnabled(False)
        try:
            if manager is not None:
                layout = space.get("areas")
                if isinstance(layout, dict):
                    manager.rebuild(copy.deepcopy(layout))
                    for d in w._sidebar_docks():
                        d.hide()                 # the tray is an area there
                else:
                    space["areas"] = manager.to_dict()
            else:
                self.apply_classic()
        finally:
            w.setUpdatesEnabled(True)
        w.stage.place()

    def apply_classic(self) -> None:
        w = self.window
        saved = self.space().get("classic")
        if not isinstance(saved, dict):
            self.capture()
            return
        try:
            state = QByteArray(base64.b64decode(saved.get("state") or ""))
        except (ValueError, TypeError):
            state = None
        if state:
            w.restoreState(state)
        shown = set(saved.get("trays") or [])
        for d in w._sidebar_docks():
            w._keep_docked(d)
            d.setVisible(d.objectName() in shown)
        front = next((d for d in w._sidebar_docks()
                      if d.objectName() == saved.get("front")), None)
        if front is not None and not front.isHidden():
            front.raise_()

    # ---- the user's actions ------------------------------------------------------------
    def switch(self, index: int) -> None:
        if not (0 <= index < len(self.spaces)) or index == self.current:
            self.changed.emit()                  # put the bar back on it
            return
        self.capture()
        self.current = index
        self._show_current()
        self.save()
        self.changed.emit()

    def _show_current(self) -> None:
        """The current workspace on screen: its layout, then its
        arrangement (the layout switch builds it for areas)."""
        mode = self.mode()
        if mode is not None and mode != self.window.layout_mode():
            self.window.set_layout_mode(mode, capture=False)
        else:
            self.apply()

    def new(self) -> None:
        """A new workspace: a copy of the current arrangement, shown."""
        self.capture()
        space = copy.deepcopy(self.space())
        space.update(id=_new_id(), name=self._next_name())
        self.spaces.append(space)
        self.current = len(self.spaces) - 1
        self.save()
        self.changed.emit()

    def duplicate(self, index: int) -> None:
        if index == self.current:
            self.capture()
        space = copy.deepcopy(self.spaces[index])
        space.update(id=_new_id(), name=tr("{name} (copy)", name=space["name"]))
        self.spaces.insert(index + 1, space)
        if index < self.current:
            self.current += 1
        self.save()
        self.changed.emit()

    def rename(self, index: int, name: str) -> None:
        name = str(name).strip()
        if name and 0 <= index < len(self.spaces):
            self.spaces[index]["name"] = name
            self.save()
            self.changed.emit()

    def delete(self, index: int) -> bool:
        if len(self.spaces) <= 1 or not (0 <= index < len(self.spaces)):
            return False
        was_current = index == self.current
        del self.spaces[index]
        if index < self.current:
            self.current -= 1
        elif was_current:
            self.current = min(index, len(self.spaces) - 1)
            self._show_current()
        self.save()
        self.changed.emit()
        return True

    # ---- templates (Preferences ▸ Workspaces) -------------------------------------------
    def export_to(self, path) -> int:
        """Every workspace, as they are now, to a template file."""
        from pathlib import Path
        self.capture()
        data = {"format": TEMPLATE_FORMAT, "version": TEMPLATE_VERSION,
                "current": self.current, "workspaces": copy.deepcopy(self.spaces)}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        return len(self.spaces)

    @staticmethod
    def read_template(path) -> tuple:
        """The workspaces of a template file — ValueError when it is not
        one (nothing is touched then)."""
        from pathlib import Path
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(tr("The file could not be read: {error}", error=exc))
        if not isinstance(data, dict) or data.get("format") != TEMPLATE_FORMAT:
            raise ValueError(tr("It is not an IngeTrazo workspaces template."))
        spaces = [s for s in data.get("workspaces") or []
                  if isinstance(s, dict) and str(s.get("name") or "").strip()]
        if not spaces:
            raise ValueError(tr("The template has no workspaces."))
        for s in spaces:
            if s.get("mode") not in ("classic", "areas"):
                s["mode"] = None
            for key in ("classic", "areas"):
                if not isinstance(s.get(key), dict):
                    s[key] = None
        current = data.get("current", 0)
        current = current if isinstance(current, int) and 0 <= current < len(spaces) else 0
        return spaces, current

    def import_from(self, path, replace: bool) -> int:
        """Load a template: replace these workspaces (and show its current
        one) or add its own after them (a taken name gets a number)."""
        spaces, current = self.read_template(path)
        if replace:
            self.capture()
            self.spaces = [dict(s, id=_new_id()) for s in spaces]
            self.current = current
            self._show_current()
        else:
            names = set(self.names())
            for s in spaces:
                s = dict(s, id=_new_id())
                base, n = s["name"], 2
                while s["name"] in names:
                    s["name"] = f"{base} ({n})"
                    n += 1
                names.add(s["name"])
                self.spaces.append(s)
        self.save()
        self.changed.emit()
        return len(spaces)

    def reset(self) -> None:
        """One workspace again — «Workspace 1», with the window as it is."""
        self.spaces = [self._blank(1)]
        self.current = 0
        self.capture()
        self.save()
        self.changed.emit()

    def ask_rename(self, index: int) -> None:
        name, ok = QInputDialog.getText(
            self.window, tr("Rename workspace"), tr("Name:"),
            text=self.spaces[index]["name"])
        if ok:
            self.rename(index, name)

    def ask_delete(self, index: int) -> None:
        if len(self.spaces) <= 1:
            self.window.statusBar().showMessage(
                tr("The last workspace cannot be deleted."), 4000)
            return
        if QMessageBox.question(
                self.window, tr("Delete workspace"),
                tr("Delete the workspace «{name}»? Its arrangement goes with "
                   "it; the document is not touched.",
                   name=self.spaces[index]["name"])) == QMessageBox.Yes:
            self.delete(index)

    def context_menu(self, index: int, global_pos) -> None:
        menu = QMenu(self.window)
        menu.addAction(tr("Rename…"), lambda: self.ask_rename(index))
        menu.addAction(tr("Duplicate"), lambda: self.duplicate(index))
        menu.addSeparator()
        dele = menu.addAction(tr("Delete…"), lambda: self.ask_delete(index))
        dele.setEnabled(len(self.spaces) > 1)
        menu.exec(global_pos)

    # ---- Window ▸ Workspace ---------------------------------------------------------------
    def build_menu(self, parent_menu: QMenu) -> QMenu:
        menu = QMenu(tr("Workspace"), parent_menu)
        menu.aboutToShow.connect(lambda m=menu: self._fill_menu(m))
        parent_menu.addMenu(menu)
        self._menu = menu
        self._fill_menu(menu)
        return menu

    def _fill_menu(self, menu: QMenu) -> None:
        old = getattr(self, "_display_menu", None)
        menu.clear()
        if old is not None:
            old.deleteLater()
        group = QActionGroup(menu)
        for i, name in enumerate(self.names()):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(i == self.current)
            act.setActionGroup(group)
            act.triggered.connect(lambda _=False, i=i: self.switch(i))
        menu.addSeparator()
        new = menu.addAction(tr("New workspace"))
        new.setStatusTip(tr("A new workspace, starting as a copy of this one."))
        new.triggered.connect(self.new)
        menu.addSeparator()
        display = QMenu(tr("Display"), menu)
        menu.addMenu(display)
        self._display_menu = display
        vgroup = QActionGroup(display)
        for view, text in ((VIEW_LIST, tr("Drop-down list")), (VIEW_TABS, tr("Tabs"))):
            act = display.addAction(text)
            act.setCheckable(True)
            act.setChecked(self.view() == view)
            act.setActionGroup(vgroup)
            act.triggered.connect(lambda _=False, v=view: self.set_view(v))

    # ---- the bar -----------------------------------------------------------------------------
    @staticmethod
    def view() -> str:
        v = str(QSettings().value(VIEW_KEY, VIEW_TABS))
        return v if v in (VIEW_LIST, VIEW_TABS) else VIEW_TABS

    def set_view(self, view: str) -> None:
        QSettings().setValue(VIEW_KEY, view)
        self.install_bar()

    def install_bar(self) -> None:
        """The list or the tabs on the menu bar's line (a slim toolbar under
        it where the menu bar is the system's, as on macOS)."""
        w = self.window
        mb = w.menuBar()
        if self.bar is not None:
            self.bar.detach()
            self.bar.hide()                      # now, not at the next loop
            self.bar.setParent(None)
            self.bar.deleteLater()
            self.bar = None
        bar = WorkspaceBar(self, self.view())
        self.bar = bar
        bar.refresh()                            # filled before it is sized
        if mb.isNativeMenuBar():
            if self._toolbar is None:
                self._toolbar = QToolBar(tr("Workspaces"), w)
                self._toolbar.setObjectName("workspaces_toolbar")
                self._toolbar.setMovable(False)
                w.addToolBar(Qt.TopToolBarArea, self._toolbar)
                w.insertToolBarBreak(self._toolbar)
            self._toolbar.clear()
            self._toolbar.addWidget(bar)
        elif bar.view == VIEW_TABS:
            # Laid over the menu bar right after its menus. Not a corner
            # widget: a corner's width becomes the menu bar's minimum, and
            # the window grew with it.
            old = mb.cornerWidget(Qt.TopRightCorner)
            if old is not None:
                mb.setCornerWidget(None, Qt.TopRightCorner)
                old.hide()
            bar.setParent(mb)
            bar.attach(mb)
            bar.show()
        else:
            corner = Qt.TopRightCorner
            old = mb.cornerWidget(corner)
            if old is not None and old is not bar:
                old.hide()
            mb.setCornerWidget(bar, corner)
            bar.attach(mb)
            bar.show()
        self.changed.connect(bar.refresh)
        bar.refresh()

    def _corner_changed(self, bar) -> None:
        """The list's names changed width: the menu bar sizes its corner
        only when it is set, so set it again."""
        mb = self.window.menuBar()
        if not mb.isNativeMenuBar() and mb.cornerWidget(Qt.TopRightCorner) is bar:
            mb.setCornerWidget(bar, Qt.TopRightCorner)
            bar.show()


class WorkspaceBar(QWidget):
    """«Workspace: [list ▾]» at the right of the menu bar, or the tabs right
    after the menus (Workspace 1 · Workspace 2 · … · + New)."""

    def __init__(self, manager: WorkspaceManager, view: str) -> None:
        super().__init__()
        self.manager = manager
        self.view = view
        self.setObjectName("workspace_bar")
        self._menubar = None
        self._busy = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(6)
        self.combo: Optional[QComboBox] = None
        self.tabs: Optional[QTabBar] = None
        if view == VIEW_LIST:
            lay.addWidget(QLabel(tr("Workspace:")))
            self.combo = QComboBox()
            self.combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            self.combo.activated.connect(self._combo_picked)
            self.combo.setContextMenuPolicy(Qt.CustomContextMenu)
            self.combo.customContextMenuRequested.connect(
                lambda pos: manager.context_menu(
                    manager.current, self.combo.mapToGlobal(pos)))
            # Opened, the workspace under the pointer (lit blue) has its
            # own right-click menu: Rename, Duplicate, Delete.
            view = self.combo.view()
            view.setContextMenuPolicy(Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(self._list_menu)
            lay.addWidget(self.combo)
        else:
            # Set apart from the menus: a lighter separator, and the tabs in a
            # strip of their own, light blue-grey with a border.
            line = QFrame()
            line.setObjectName("workspace_separator")
            line.setFixedWidth(1)
            lay.addWidget(line)
            self.strip = QFrame()
            self.strip.setObjectName("workspace_strip")
            strip_lay = QHBoxLayout(self.strip)
            strip_lay.setContentsMargins(2, 1, 2, 1)
            strip_lay.setSpacing(0)
            self.tabs = QTabBar()
            self.tabs.setDocumentMode(True)
            self.tabs.setDrawBase(False)
            self.tabs.setExpanding(False)
            self.tabs.setUsesScrollButtons(True)
            self.tabs.setElideMode(Qt.ElideRight)
            self.tabs.tabBarClicked.connect(self._tab_clicked)
            self.tabs.tabBarDoubleClicked.connect(self._tab_double_clicked)
            self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
            self.tabs.customContextMenuRequested.connect(self._tab_menu)
            strip_lay.addWidget(self.tabs)
            lay.addWidget(self.strip)
            lay.addStretch(1)
            self.apply_colors()

    def apply_colors(self) -> None:
        """Light blue-grey strip, lighter separator — readable on the dark
        theme and on the light one."""
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QApplication
        dark = QApplication.palette().color(QPalette.Window).lightness() < 128
        if dark:
            sep, bg, border, text, hover, sel, sel_text, line = (
                "#8a94a3", "#3a4657", "#6b7c93", "#d6dde6", "#465569", "#52637a",
                "#bfdbfe", "#60a5fa")
        else:
            sep, bg, border, text, hover, sel, sel_text, line = (
                "#9aa3ad", "#dde5ef", "#a9b8cb", "#1f2a37", "#ccd8e6", "#c2d1e3",
                "#1d4ed8", "#2563eb")
        self.setStyleSheet(
            f"#workspace_separator {{ background: {sep}; margin: 4px 2px; }}"
            f"#workspace_strip {{ background: {bg}; border: 1px solid {border};"
            " border-radius: 4px; }"
            f"#workspace_strip QTabBar {{ background: transparent; }}"
            f"#workspace_strip QTabBar::tab {{ color: {text}; background: transparent;"
            " padding: 1px 10px; margin: 0; border: none; }"
            f"#workspace_strip QTabBar::tab:hover {{ background: {hover}; }}"
            f"#workspace_strip QTabBar::tab:selected {{ background: {sel};"
            f" color: {sel_text}; font-weight: 600;"
            f" border-bottom: 2px solid {line}; }}")

    def changeEvent(self, event) -> None:
        from PySide6.QtCore import QEvent
        super().changeEvent(event)
        if event.type() == QEvent.PaletteChange and self.tabs is not None:
            self.apply_colors()

    # ---- placement: tabs start right after the menus -----------------------------------
    def attach(self, menubar) -> None:
        self._menubar = menubar
        menubar.installEventFilter(self)
        self._fit()

    def detach(self) -> None:
        if self._menubar is not None:
            try:
                self._menubar.removeEventFilter(self)
            except RuntimeError:
                pass

    def eventFilter(self, obj, event):  # noqa: N802 — Qt override
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Resize, QEvent.LayoutRequest):
            self._fit()
        return False

    def _fit(self) -> None:
        """Tabs: from the end of the menus to the menu bar's right edge,
        its full height. List: a corner widget, its own width."""
        mb = self._menubar
        if mb is None or self.view != VIEW_TABS:
            return
        right = 0
        for act in mb.actions():
            if act.isVisible():
                right = max(right, mb.actionGeometry(act).right())
        x = right + 4
        rect = (x, 0, max(0, mb.width() - x - 4), mb.height())
        if self.geometry().getRect() != rect:
            self.setGeometry(*rect)
        self.raise_()

    # ---- contents ------------------------------------------------------------------------
    def refresh(self) -> None:
        m = self.manager
        self._busy = True
        try:
            if self.combo is not None:
                self.combo.clear()
                for name in m.names():
                    self.combo.addItem(name)
                self.combo.insertSeparator(self.combo.count())
                self.combo.addItem(tr("New…"), _NEW)
                self.combo.setCurrentIndex(m.current)
                # As wide as the longest name: a corner widget is not stretched.
                longest = max([len(n) for n in m.names()] + [len(tr("New…"))])
                self.combo.setMinimumContentsLength(longest)
                self.combo.setSizeAdjustPolicy(
                    QComboBox.AdjustToMinimumContentsLengthWithIcon)
                self.updateGeometry()
                if self._menubar is not None:
                    m._corner_changed(self)
            if self.tabs is not None:
                while self.tabs.count():
                    self.tabs.removeTab(0)
                for name in m.names():
                    self.tabs.addTab(name)
                new = self.tabs.addTab("＋ " + tr("New"))
                self.tabs.setTabToolTip(new, tr("A new workspace, starting as a copy "
                                                "of this one."))
                self.tabs.setCurrentIndex(m.current)
        finally:
            self._busy = False
        self._fit()

    def _combo_picked(self, index: int) -> None:
        if self._busy:
            return
        # After the click: the switch rebuilds this very list.
        if self.combo.itemData(index) == _NEW:
            QTimer.singleShot(0, self.manager.new)
        else:
            QTimer.singleShot(0, lambda: self.manager.switch(index))

    def _list_menu(self, pos) -> None:
        view = self.combo.view()
        row = view.indexAt(pos).row()
        if not (0 <= row < len(self.manager.spaces)):
            return                               # the separator, «New…»
        at = view.viewport().mapToGlobal(pos)
        self.combo.hidePopup()
        self.manager.context_menu(row, at)

    def _tab_clicked(self, index: int) -> None:
        if self._busy or index < 0:
            return
        # After the click: the switch rebuilds these very tabs.
        if index == self.tabs.count() - 1:       # «+ New»
            QTimer.singleShot(0, self.manager.new)
        else:
            QTimer.singleShot(0, lambda: self.manager.switch(index))

    def _tab_double_clicked(self, index: int) -> None:
        if 0 <= index < self.tabs.count() - 1:
            self.manager.ask_rename(index)

    def _tab_menu(self, pos) -> None:
        index = self.tabs.tabAt(pos)
        if 0 <= index < self.tabs.count() - 1:
            self.manager.context_menu(index, self.tabs.mapToGlobal(pos))


class WorkspacesPrefsPanel(QWidget):
    """Preferences ▸ Workspaces: the list, and the templates — Export,
    Import (replace or add), Reset. Done at once, not on OK."""

    def __init__(self, window, parent=None) -> None:
        from PySide6.QtWidgets import (QListWidget, QPushButton,
                                       QVBoxLayout)
        super().__init__(parent)
        self.window = window
        self.manager: WorkspaceManager = window.workspaces
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("Your workspaces and their layout:")))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.NoSelection)
        lay.addWidget(self.list, 1)
        note = QLabel(tr(
            "A template keeps every workspace — its layout and how it was "
            "arranged — in a file you can take to another computer or keep "
            "as a starting point. These buttons act at once."))
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        lay.addWidget(note)
        row = QHBoxLayout()
        self.export_button = QPushButton(tr("Export…"))
        self.export_button.setToolTip(tr("Save every workspace to a template file."))
        self.export_button.clicked.connect(self._export)
        self.import_button = QPushButton(tr("Import…"))
        self.import_button.setToolTip(tr("Load the workspaces of a template file."))
        self.import_button.clicked.connect(self._import)
        self.reset_button = QPushButton(tr("Reset"))
        self.reset_button.setToolTip(tr("Back to one workspace, with the window as it is now."))
        self.reset_button.clicked.connect(self._reset)
        for b in (self.export_button, self.import_button, self.reset_button):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.manager.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        layouts = {"classic": tr("Classic layout"), "areas": tr("Areas (Blender style)")}
        for i, space in enumerate(self.manager.spaces):
            mode = space.get("mode") or self.window.layout_mode()
            text = f"{space['name']} — {layouts.get(mode, mode)}"
            if i == self.manager.current:
                text += "   ✓"
            self.list.addItem(text)

    def _filter(self) -> str:
        return tr("IngeTrazo workspaces (*{ext})", ext=TEMPLATE_SUFFIX)

    def _export(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export workspaces"), "espacios" + TEMPLATE_SUFFIX, self._filter())
        if not path:
            return
        if not path.lower().endswith(TEMPLATE_SUFFIX):
            path += TEMPLATE_SUFFIX
        try:
            n = self.manager.export_to(path)
        except OSError as exc:
            QMessageBox.warning(self, tr("Export workspaces"), str(exc))
            return
        self.window.statusBar().showMessage(
            tr("{n} workspace(s) exported.", n=n), 4000)

    def _import(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Import workspaces"), "", self._filter())
        if not path:
            return
        try:
            self.manager.read_template(path)             # check before asking
        except ValueError as exc:
            QMessageBox.warning(self, tr("Import workspaces"), str(exc))
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("Import workspaces"))
        box.setText(tr("Replace your workspaces with the template's, or add "
                       "them after yours?"))
        replace = box.addButton(tr("Replace"), QMessageBox.AcceptRole)
        add = box.addButton(tr("Add"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel).setText(tr("Cancel"))
        box.exec()
        if box.clickedButton() not in (replace, add):
            return
        n = self.manager.import_from(path, replace=box.clickedButton() is replace)
        self.window.statusBar().showMessage(
            tr("{n} workspace(s) imported.", n=n), 4000)

    def _reset(self) -> None:
        if QMessageBox.question(
                self, tr("Reset workspaces"),
                tr("Keep only one workspace, «{name}», with the window as it is "
                   "now? The others are deleted.",
                   name=tr("Workspace {n}", n=1))) == QMessageBox.Yes:
            self.manager.reset()
