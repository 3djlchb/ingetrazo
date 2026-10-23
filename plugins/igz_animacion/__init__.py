# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Animation: the document's scenes as keyframes — a camera (and section
plane) walk between them, previewed on its timeline and rendered to an image
sequence or a video, by the viewport itself or by Blender. A tab of the side
tray, «Animation».

The preview timeline is the Timeline extension's (``igz_timeline``): the
ticked scenes are its «Scenes» track. Without it the tab works the same,
with no preview timeline.

Bundled with the application; its stem, ``igz_animacion``, is the name the
stand-alone plugin had, so the documents it saved open their animation
here unchanged, and an old copy left in the user's plugin folder is not
loaded twice (the bundled one goes first)."""
from core.i18n import tr

PLUGIN_NAME = "Animation"
PLUGIN_VERSION = "0.24.0"


def setup(app):
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

    # The shared clock, whenever the Timeline extension is there (it may
    # load before or after this one).
    if hasattr(app, "use"):
        app.use("timeline", panel.attach_timeline)

    app.add_menu_action(tr("Animation…"), lambda: app.show_panel(side_dock))
    app.window._igz_animacion_runtime = {"panel": panel, "side_dock": side_dock}
    return lambda: app.show_panel(side_dock)
