# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Animation: the document's scenes as keyframes — a camera (and section
plane) walk between them, previewed on a timeline under the viewport and
rendered to an image sequence or a video, by the viewport itself or by
Blender. A tab of the side tray, «Animation».

Bundled with the application; its stem, ``igz_animacion``, is the name the
stand-alone plugin had, so the documents it saved open their animation
here unchanged, and an old copy left in the user's plugin folder is not
loaded twice (the bundled one goes first)."""
from PySide6.QtCore import Qt

from core.i18n import tr

PLUGIN_NAME = "Animation"
PLUGIN_VERSION = "0.23.0"


def setup(app):
    from .timeline import AnimationTimeline
    from .ui import AnimacionPanel

    # One panel per window, however many times setup runs (an extension
    # refresh re-runs it): two panels would both answer «New scene».
    runtime = getattr(app.window, "_igz_animacion_runtime", None)
    if isinstance(runtime, dict) and runtime.get("side_dock") is not None:
        dock = runtime["side_dock"]
        return lambda: app.show_panel(dock)

    panel = AnimacionPanel(app)
    side_dock = app.add_panel(tr("Animation"), panel, name="animacion")
    panel.set_dock(side_dock)

    # The timeline is a child of the viewport, along its bottom edge: it
    # takes nothing from the side tray, and hides with «Show the timeline».
    timeline = AnimationTimeline()
    viewport = app.viewport
    timeline.setParent(viewport)
    timeline.setWindowFlags(Qt.Widget)
    timeline.setMinimumHeight(0)
    timeline.setMaximumHeight(132)

    def fit_timeline():
        try:
            h = min(126, max(96, viewport.height() // 5))
            timeline.setGeometry(0, viewport.height() - h, viewport.width(), h)
            timeline.raise_()
        except RuntimeError:            # the viewport is being torn down
            pass

    panel._fit_timeline_width = fit_timeline
    viewport.installEventFilter(panel)
    fit_timeline()
    panel.set_timeline(timeline, timeline)

    app.add_menu_action(tr("Animation…"), lambda: app.show_panel(side_dock))
    app.window._igz_animacion_runtime = {"panel": panel, "side_dock": side_dock,
                                         "timeline": timeline}
    return lambda: app.show_panel(side_dock)
