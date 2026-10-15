# SPDX-License-Identifier: GPL-3.0-or-later
"""A dedicated bottom timeline window, connected to ``igz_animacion``."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QLabel, QWidget

from core.i18n import tr


EDITOR_ID = "timeline_editor"


def _timeline_area_create(window, area):
    """Move the shared timeline widget from the classic dock into an area."""
    runtime = window._timeline_plugin_runtime
    dock = runtime["dock"]
    timeline = runtime["animation_timeline"]
    runtime["dock_was_visible"] = not dock.isHidden()
    runtime["moving_to_area"] = True
    dock.hide()
    timeline.setParent(None)
    dock.setWidget(QWidget(dock))
    timeline.setParent(area)
    runtime["timeline_in_area"] = True
    return timeline


def _timeline_area_release(window, widget):
    """Return the shared timeline widget to the classic bottom dock."""
    runtime = window._timeline_plugin_runtime
    dock = runtime["dock"]
    dock.setWidget(widget)
    runtime["timeline_in_area"] = False
    runtime["moving_to_area"] = False
    if runtime.get("dock_was_visible", False):
        dock.show()


def setup(app):
    """Host Animation's live timeline in a dock shown from the Window menu."""
    runtime = getattr(app.window, "_timeline_plugin_runtime", None)
    if isinstance(runtime, dict) and runtime.get("dock") is not None:
        return lambda: runtime["dock"].show()

    dock = QDockWidget(tr("Timeline"), app.window)
    dock.setObjectName("dock_timeline")
    dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)

    animation = getattr(app.window, "_igz_animacion_runtime", None)
    animation_panel = None
    timeline = None
    if isinstance(animation, dict) and animation.get("timeline") is not None:
        timeline = animation["timeline"]
        timeline.setMinimumHeight(118)
        timeline.setMaximumHeight(16777215)
        dock.setWidget(timeline)
        animation_panel = animation.get("panel")
    else:
        timeline = QLabel(tr(
            "Enable the igz_animacion extension to use the animation timeline."))
        dock.setWidget(timeline)

    app.window.addDockWidget(Qt.BottomDockWidgetArea, dock)
    dock.hide()
    if animation_panel is not None:
        animation_panel.set_timeline(timeline, dock)

    app.window._timeline_plugin_runtime = {
        "dock": dock,
        "action": None,
        "animation_timeline": timeline,
        "dock_was_visible": not dock.isHidden(),
        "moving_to_area": False,
        "timeline_in_area": False,
    }

    window_menu = getattr(app.window, "_window_menu", None)
    action = None
    if window_menu is not None:
        action = dock.toggleViewAction()
        action.setText(tr("Timeline"))
        action.setStatusTip(tr("Show or hide the timeline window."))
        window_menu.addAction(action)

    app.window._timeline_plugin_runtime["action"] = action

    if animation_panel is not None:
        def sync_animation_checkbox(visible):
            if app.window._timeline_plugin_runtime.get("moving_to_area"):
                return
            checkbox = getattr(animation_panel, "show_timeline", None)
            if checkbox is not None and checkbox.isChecked() != bool(visible):
                checkbox.setChecked(bool(visible))

        dock.visibilityChanged.connect(sync_animation_checkbox)

    app.add_editor(EDITOR_ID, "Timeline", _timeline_area_create,
                   category="Animation", release=_timeline_area_release,
                   single=True)
    return lambda: dock.show()
