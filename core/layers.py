# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Layer / tag system: visibility and locking (tags).

A layer never *owns* geometry — it is a label an entity carries (faces via
``attrs["layer"]``, edges via their ``layer`` slot, groups via ``.layer``).
Hiding a layer removes its entities from render, picking and box selection;
locking keeps them visible but unpickable. Everything unlabelled lives on the
default layer, which always exists and cannot be removed.

This is what makes the '2D that emerges' workflow real: structure / walls /
plumbing / furniture live on their own layers of one model, and a top view in
parallel projection with the right layers on IS the plan drawing.
"""
from __future__ import annotations

import uuid

DEFAULT_LAYER = "Layer 0"

#: The default layer's identity. A constant, not a random id: that layer is
#: not always written to the file (a document with only it stores no layer
#: list), and its uid must be the same every time it is read back.
DEFAULT_LAYER_UID = "layer-default"

# Where an imported photogrammetric survey lands (Track G, G6). Its own layer
# by default, not the default one: the whole point of importing a survey is to
# draw on top of it, and you need to switch it off to look at what you drew —
# which would take your model with it if they shared a layer. A plain stored
# name like DEFAULT_LAYER, not a translated one: layer names are document data
# and must not change with the interface language.
SURVEY_LAYER = "Survey"


class Layer:
    """A tag with display state.

    ``name`` is only a label — the user renames it at will. ``uid`` is the
    layer's identity: assigned once, saved with the document, and never
    changed (not by a rename, a save/load or an undo). Anything that must
    keep pointing at "this layer" through a rename — the scenes that hide it
    — refers to the uid. (Entities still carry the NAME, which a rename
    rewrites; see ``LayersPanel._rename``.)
    """

    def __init__(self, name: str, visible: bool = True,
                 locked: bool = False, uid: str | None = None) -> None:
        self.name = name
        self.visible = visible
        self.locked = locked
        self.uid = uid or (DEFAULT_LAYER_UID if name == DEFAULT_LAYER
                           else uuid.uuid4().hex)

    def to_dict(self) -> dict:
        entry: dict = {"name": self.name, "uid": self.uid}
        if not self.visible:
            entry["visible"] = False
        if self.locked:
            entry["locked"] = True
        return entry

    @classmethod
    def from_dict(cls, raw: dict) -> "Layer":
        return cls(raw.get("name", DEFAULT_LAYER),
                   visible=raw.get("visible", True),
                   locked=raw.get("locked", False),
                   uid=raw.get("uid") or None)       # old files: made fresh


def ensure_unique_uids(layers) -> None:
    """Give a new uid to any layer whose uid another one already has (a
    hand-edited or merged file), and never let a layer other than the
    default hold the default's. The first holder keeps its uid."""
    seen: set[str] = set()
    for ly in layers:
        if ly.uid in seen or (ly.uid == DEFAULT_LAYER_UID
                              and ly.name != DEFAULT_LAYER):
            ly.uid = uuid.uuid4().hex
        seen.add(ly.uid)


def layer_of(entity) -> str:
    """The layer name an entity carries (default when unlabelled)."""
    attrs = getattr(entity, "attrs", None)
    if attrs is not None:                          # Face
        return attrs.get("layer") or DEFAULT_LAYER
    return getattr(entity, "layer", None) or DEFAULT_LAYER


def assign_layer(entity, name: str) -> None:
    """Label an entity with a layer (default name clears the label)."""
    value = None if name == DEFAULT_LAYER else name
    attrs = getattr(entity, "attrs", None)
    if attrs is not None:                          # Face
        if value is None:
            attrs.pop("layer", None)
        else:
            attrs["layer"] = value
    elif hasattr(entity, "layer"):
        entity.layer = value
