# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Timeline: one clock for every extension that animates.

It owns the frame, the duration and the frame rate, plays and loops, and
shows a ruler with one row per **track**. It knows nothing of what is
animated: an extension asks for it with ``app.use("timeline", fn)`` and
hangs its own track on the clock — Animation's scenes are the first; a sun
study, construction phases or a camera path can follow without touching
this plugin. An extension must keep working when the Timeline is absent.

Where it shows: in the classic layout, a panel along the bottom of the
window, opened from Window ▸ Timeline (as Terrain profile); with areas,
the «Timeline» editor of an area (Animation category), and the Window
entry is locked."""
from core.i18n import tr

PLUGIN_NAME = "Timeline"
PLUGIN_VERSION = "1.0.0"

#: The area editor and the bottom panel keep the names the first timeline
#: had, so saved layouts and areas find it again.
EDITOR_ID = "timeline_editor"
DOCK_NAME = "dock_timeline"
#: Whether the bottom panel was open (classic layout); before this plugin,
#: Animation kept it as «Show the timeline».
OPEN_KEY = "igz_timeline/open"
OLD_OPEN_KEY = "igz_animacion/show_timeline"


def setup(app):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QDockWidget, QWidget

    from .clock import TimelineClock
    from .editor import TimelineEditor

    win = app.window
    runtime = getattr(win, "_igz_timeline_runtime", None)
    if isinstance(runtime, dict):               # setup run again (refresh)
        return lambda: runtime["clock"].show_editor(True)

    clock = TimelineClock(win)
    editor = TimelineEditor(clock)
    dock = QDockWidget(tr("Timeline"), win)
    dock.setObjectName(DOCK_NAME)
    dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
    dock.setWidget(editor)
    win.addDockWidget(Qt.BottomDockWidgetArea, dock)
    dock.hide()
    runtime = win._igz_timeline_runtime = {
        "clock": clock, "editor": editor, "dock": dock, "in_area": None}

    def areas() -> bool:
        return getattr(win, "area_manager", None) is not None

    def shown() -> bool:
        if runtime["in_area"] is not None:
            return True
        return not dock.isHidden()

    def show(on: bool, quiet: bool = False) -> None:
        if on and areas():
            if runtime["in_area"] is None and not quiet:
                _say(win, tr("With areas, pick «Timeline» as an area's "
                             "editor."))
            return
        if runtime["in_area"] is None:
            dock.setVisible(on)
            # visibilityChanged only comes for a window on screen: the menu
            # entry and Animation's checkbox follow from here too.
            sync_action()

    clock._show_editor = show
    clock._editor_shown = shown

    # Window ▸ Timeline, as Window ▸ Terrain profile.
    action = QAction(tr("Timeline"), win)
    action.setCheckable(True)
    action.setStatusTip(tr("Show or hide the timeline along the bottom of "
                           "the window."))
    action.triggered.connect(lambda on: (show(on), sync_action()))
    runtime["action"] = action
    menu = getattr(win, "_window_menu", None)
    if menu is not None:
        before = getattr(win, "_act_profile", None)
        acts = menu.actions()
        if before is not None and before in acts:
            i = acts.index(before) + 1
            menu.insertAction(acts[i], action) if i < len(acts) \
                else menu.addAction(action)
        else:
            menu.addAction(action)

    def sync_action(*_args) -> None:
        if not areas() and runtime["in_area"] is None:
            QSettings().setValue(OPEN_KEY, not dock.isHidden())
        action.blockSignals(True)
        action.setChecked(shown())
        action.setEnabled(not areas())
        action.blockSignals(False)
        action.setStatusTip(
            tr("With areas, pick «Timeline» as an area's editor.")
            if areas() else
            tr("Show or hide the timeline along the bottom of the window."))
        clock.editorShownChanged.emit(shown())

    dock.visibilityChanged.connect(sync_action)

    # Areas: the editor moves into the area, and back to the panel after.
    def into_area(_window, area):
        runtime["was_open"] = not dock.isHidden()
        dock.hide()
        dock.setWidget(QWidget(dock))           # the editor is lent out
        editor.setParent(area)
        runtime["in_area"] = area
        sync_action()
        return editor

    def out_of_area(_window, widget):
        runtime["in_area"] = None
        dock.setWidget(widget)
        if runtime.pop("was_open", False) and not areas():
            dock.show()
        sync_action()

    app.add_editor(EDITOR_ID, "Timeline", into_area, category="Animation",
                   release=out_of_area, single=True)

    def layout_changed(mode) -> None:
        # Into areas, the bottom panel is put away (its place is an area);
        # back in the classic layout it returns if it was open.
        if mode == "areas":
            runtime["classic_open"] = not dock.isHidden()
            dock.hide()
        elif runtime.pop("classic_open", False) and runtime["in_area"] is None:
            dock.show()
        sync_action()

    signal = getattr(win, "layoutModeChanged", None)
    if signal is not None:
        signal.connect(layout_changed)
    # Open as it was left — the first time, as Animation's old «Show the
    # timeline» had it.
    st = QSettings()
    was_open = st.value(OPEN_KEY, st.value(OLD_OPEN_KEY, False, bool), bool)
    if was_open:
        show(True, quiet=True)
    sync_action()

    if hasattr(app, "provide"):
        app.provide("timeline", clock)
    return lambda: show(True)


def _say(win, text: str) -> None:
    vp = getattr(win, "viewport", None)
    flash = getattr(vp, "flash_status", None)
    if callable(flash):
        flash(text, 4000)
    else:
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage(text, 4000)
