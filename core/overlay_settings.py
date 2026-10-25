# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent visibility of the viewport's overlays, in two groups.

**Native** overlays are the viewport's own, the ones IngeTrazo itself
draws (upstream): axes, guides, snapping, dimensions, texts… **Community**
overlays are the rest: the ones this fork added (the scene and layer
labels, Face Orientation, the Overlays button) and every extension's,
each under its extension — an extension names its overlays when it adds
them (``ExtensionApp.add_overlay(fn, name=…, title=…)``), so they can be
told apart and switched one by one.

One master switch hides all; each group has its own; each overlay its
own. Everything is on by default except Face Orientation (opt-in)."""
from PySide6.QtCore import QSettings

NATIVE = "native"
COMMUNITY = "community"

#: The viewport's own overlays: (key, English label, default).
NATIVE_OVERLAYS = (
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
)

#: The overlays this fork added to the viewport — community, not native.
FORK_OVERLAYS = (
    ("scene", "Scene name", True),
    ("layer", "Active layer", True),
    ("face_orientation", "Face Orientation", False),
    ("button", "Overlays button", True),
)

#: Every built-in overlay (key, label, default).
OVERLAYS = NATIVE_OVERLAYS + FORK_OVERLAYS

GROUP_OF = {key: NATIVE for key, _l, _d in NATIVE_OVERLAYS}
GROUP_OF.update({key: COMMUNITY for key, _l, _d in FORK_OVERLAYS})


def ext_key(extension: str, name: str) -> str:
    """The settings key of an extension's overlay."""
    return f"overlays/ext/{extension}/{name}"


class OverlayState:
    """What is shown: the master switch, the two groups, each built-in
    overlay and each extension overlay (``{(extension, name): bool}``,
    absent = on)."""

    def __init__(self, enabled=True, groups=None, items=None, ext=None):
        self.enabled = bool(enabled)
        self.groups = {NATIVE: True, COMMUNITY: True}
        self.groups.update(groups or {})
        self.items = {key: default for key, _l, default in OVERLAYS}
        self.items.update(items or {})
        self.ext = dict(ext or {})

    def visible(self, key: str) -> bool:
        """A built-in overlay: the master, its group and its own switch."""
        return (self.enabled
                and self.groups.get(GROUP_OF.get(key, NATIVE), True)
                and self.items.get(key, True))

    def ext_visible(self, extension: str, name: str) -> bool:
        """An extension's overlay: always a community one."""
        return (self.enabled and self.groups.get(COMMUNITY, True)
                and self.ext.get((extension, name), True))


def load(settings=None) -> OverlayState:
    st = settings if settings is not None else QSettings()
    items = {key: st.value(f"overlays/{key}", default, type=bool)
             for key, _label, default in OVERLAYS}
    groups = {g: st.value(f"overlays/group/{g}", True, type=bool)
              for g in (NATIVE, COMMUNITY)}
    ext = {}
    st.beginGroup("overlays/ext")
    try:
        for extension in st.childGroups():
            st.beginGroup(extension)
            for name in st.childKeys():
                ext[(extension, name)] = st.value(name, True, type=bool)
            st.endGroup()
    finally:
        st.endGroup()
    return OverlayState(st.value("overlays/enabled", True, type=bool),
                        groups, items, ext)


def save(state: OverlayState, settings=None) -> None:
    st = settings if settings is not None else QSettings()
    st.setValue("overlays/enabled", bool(state.enabled))
    for g in (NATIVE, COMMUNITY):
        st.setValue(f"overlays/group/{g}", bool(state.groups.get(g, True)))
    for key, _label, default in OVERLAYS:
        st.setValue(f"overlays/{key}", bool(state.items.get(key, default)))
    for (extension, name), on in state.ext.items():
        st.setValue(ext_key(extension, name), bool(on))
