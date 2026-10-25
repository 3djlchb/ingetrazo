# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent visibility of the viewport's inspection and drawing overlays."""
from PySide6.QtCore import QSettings


# Keep the current appearance on first use; face orientation is opt-in.
OVERLAYS = (
    ("scene", "Scene name", True),
    ("layer", "Active layer", True),
    ("axes", "Drawing axes", True),
    ("guides", "Construction guides", True),
    ("snapping", "Snap and inference markers", True),
    ("inference", "Inference and axis-lock labels", True),
    ("tools", "Tool previews and handles", True),
    ("selection", "Selection highlights and boxes", True),
    ("images", "Reference image outlines", True),
    ("sections", "Section plane frames", True),
    ("dimensions", "Dimensions", True),
    ("texts", "Text annotations", True),
    ("measurements", "On-screen measurements", True),
    ("georef", "Georeferencing overlays", True),
    ("extensions", "Extension overlays", True),
    ("face_orientation", "Face Orientation", False),
    ("button", "Overlays button", True),
)


def load(settings=None):
    st = settings if settings is not None else QSettings()
    enabled = st.value("overlays/enabled", True, type=bool)
    items = {key: st.value(f"overlays/{key}", default, type=bool)
             for key, _label, default in OVERLAYS}
    return enabled, items


def save(enabled, items, settings=None):
    st = settings if settings is not None else QSettings()
    st.setValue("overlays/enabled", bool(enabled))
    for key, _label, default in OVERLAYS:
        st.setValue(f"overlays/{key}", bool(items.get(key, default)))
