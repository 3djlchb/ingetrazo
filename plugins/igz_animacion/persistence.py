# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""Persistent project data for igz_animacion.

The plugin stores its state in IngeTrazo's document_data() namespace, so the
animation travels inside the .igz document and never needs a sidecar file.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

PLUGIN_DATA_KEY = "igz_animacion"
DOCUMENT_CONTEXT_KEY = "_igz_animacion_document_context"
SCHEMA_VERSION = 5


def new_scene_id() -> str:
    return uuid4().hex


def new_document_context_id() -> str:
    """Return a session/document identity persisted inside the current .igz.

    This is deliberately separate from scene IDs: scene indices are local to a
    document and must never be allowed to carry animation state across New/Open.
    """
    return uuid4().hex


def default_plugin_data() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "settings": {},
        "scene_selection": {"saved": False, "indices": []},
        "interpolate_section": True,
        "scenes": [],
    }


def normalize_scene_record(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    scene_id = str(raw.get("id") or "").strip() or new_scene_id()
    try:
        index = int(raw.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    name = str(raw.get("name") or f"Escena {index + 1}")
    section = raw.get("section")
    if isinstance(section, dict):
        section = deepcopy(section)
        if "active" not in section:
            section["active"] = bool(section.get("uid") is not None)
        if "planes_shown" in section:
            section["planes_shown"] = bool(section["planes_shown"])
        if "cuts_shown" in section:
            section["cuts_shown"] = bool(section["cuts_shown"])
        if "fill" in section:
            section["fill"] = bool(section["fill"])
        if section.get("uid") is not None:
            try:
                section["uid"] = int(section["uid"])
            except (TypeError, ValueError):
                section["uid"] = str(section["uid"])
    else:
        section = None
    return {
        "id": scene_id,
        "index": index,
        "name": name,
        "captured": bool(raw.get("captured", False)),
        "section": deepcopy(section),
    }


def normalize_plugin_data(raw: object) -> dict:
    data = default_plugin_data()
    if not isinstance(raw, dict):
        return data

    try:
        version = int(raw.get("version", SCHEMA_VERSION))
    except (TypeError, ValueError):
        version = SCHEMA_VERSION
    data["version"] = max(1, version)

    settings = raw.get("settings")
    if isinstance(settings, dict):
        data["settings"] = deepcopy(settings)

    selection = raw.get("scene_selection")
    if isinstance(selection, dict):
        indices = selection.get("indices", [])
        data["scene_selection"] = {
            "saved": bool(selection.get("saved", False)),
            "indices": sorted({int(i) for i in indices if isinstance(i, int) or str(i).isdigit()}),
        }

    data["interpolate_section"] = bool(raw.get("interpolate_section", True))

    scenes = raw.get("scenes", [])
    if isinstance(scenes, list):
        by_index = {}
        for item in scenes:
            record = normalize_scene_record(item)
            if record is None:
                continue
            idx = int(record.get("index", 0))
            existing = by_index.get(idx)
            if existing is None:
                by_index[idx] = record
            elif bool(record.get("captured")) and not bool(existing.get("captured")):
                # If a previous build produced two records for one scene, keep
                # the captured one during migration.
                by_index[idx] = record
        normalized = [by_index[i] for i in sorted(by_index)]
        data["scenes"] = normalized
    return data


def remove_scene_record(records: list, deleted_index: int) -> list:
    """Remove one scene record and shift following indices left.

    The native IngeTrazo scene list is positional. The plugin therefore keeps
    the animation record attached to the surviving scene when a scene is
    deleted from the middle of that list.
    """
    try:
        deleted_index = int(deleted_index)
    except (TypeError, ValueError):
        return list(records or [])

    result = []
    for raw in list(records or []):
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index", -1))
        except (TypeError, ValueError):
            continue
        if index == deleted_index:
            continue
        record = deepcopy(raw)
        if index > deleted_index:
            record["index"] = index - 1
        result.append(record)
    return result


def migrate_legacy_data(document: dict) -> dict:
    """Build the v1 namespace from the plugin's pre-v0.22.9 top-level data."""
    plugin = default_plugin_data()
    plugin["settings"] = {
        key: deepcopy(document[key])
        for key in (
            "image_type", "resolution_x", "resolution_y", "frames_between",
            "fps", "fps_custom_mode", "fps_custom", "crf", "codec", "duration",
            "file_type", "image_prefix",
        )
        if key in document
    }
    plugin["scene_selection"] = {
        "saved": bool(document.get("scene_selection_saved", False)),
        "indices": list(document.get("selected_scene_indices", []))
        if isinstance(document.get("selected_scene_indices", []), list) else [],
    }
    plugin["interpolate_section"] = bool(document.get("interpolate_section", True))

    legacy = document.get("scene_animation_states", {})
    if isinstance(legacy, dict):
        for index, (key, raw_state) in enumerate(legacy.items()):
            if not isinstance(raw_state, dict):
                continue
            # Legacy keys were "index:name". Preserve the old index/name when
            # possible; refresh_scenes() will reconcile them with current views.
            name = str(key).split(":", 1)[1] if ":" in str(key) else str(key)
            try:
                old_index = int(str(key).split(":", 1)[0])
            except (TypeError, ValueError):
                old_index = index
            plugin["scenes"].append({
                "id": new_scene_id(),
                "index": old_index,
                "name": name,
                "captured": bool(raw_state.get("captured", False)),
                "section": deepcopy(raw_state.get("section"))
                if isinstance(raw_state.get("section"), dict) else None,
            })
    return plugin


def get_plugin_data(document: dict) -> tuple[dict, bool]:
    """The animation data in this extension's slot of the document, and
    whether it was stored in an older layout (to be rewritten).

    ``document_data()`` is already this extension's own slot; since v0.22.66
    the data sits there directly. Earlier builds wrapped it once more under
    ``igz_animacion`` (with a ``_igz_animacion_document_context`` beside
    it), and older ones kept loose keys: both are still read."""
    if not isinstance(document, dict):
        return default_plugin_data(), False
    nested = document.get(PLUGIN_DATA_KEY)
    if isinstance(nested, dict):
        return normalize_plugin_data(nested), True
    if any(k in document for k in ("version", "settings", "scenes", "scene_selection")):
        return normalize_plugin_data(document), False
    if any(k in document for k in ("scene_animation_states", "image_type", "resolution_x")):
        return migrate_legacy_data(document), True
    return default_plugin_data(), False
