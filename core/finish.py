# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A material's FINISH: how its surface answers light (issue #181).

The viewport paints a colour or a picture; a photorealistic render also
needs to know whether that surface is matte plaster, polished marble,
steel, glass or water. IngeTrazo stores that per named material
(``Material.finish`` in the document's registry); where nobody chose one,
it is guessed from the material's name and its picture's file name, which
already say it in most models («water_calm», «[Water Pool Light]»,
«Metal_10_1K», «Marble_07»…), and a translucent paint reads as glass.

The finish travels in the GLB twice: as plain glTF (roughness, metalness,
and ``KHR_materials_transmission``/``ior`` for glass and water), which any
program understands, and as ``extras["ingetrazo_finish"]``, which the
Render with Blender script turns into a full Blender material.
"""
from __future__ import annotations

import re
from typing import Optional

#: ``auto`` means "guess"; the rest are what a user can pick.
FINISHES = ("matte", "satin", "gloss", "metal", "glass", "water")
AUTO = "auto"

#: English labels for the menu (translated with tr() where shown).
LABELS = {
    AUTO: "Automatic (from the name)",
    "matte": "Matte",
    "satin": "Satin",
    "gloss": "Gloss",
    "metal": "Metal",
    "glass": "Glass",
    "water": "Water",
}

#: glTF PBR values per finish: (roughness, metallic, transmission, ior).
PBR = {
    "matte": (0.9, 0.0, 0.0, 1.5),
    "satin": (0.45, 0.0, 0.0, 1.5),
    "gloss": (0.12, 0.0, 0.0, 1.5),
    "metal": (0.3, 1.0, 0.0, 1.5),
    "glass": (0.02, 0.0, 1.0, 1.45),
    "water": (0.03, 0.0, 0.0, 1.33),
}

# Keywords, Spanish / English / Portuguese, matched as whole words or word
# starts in the name and the picture's file name. Order matters: water and
# glass before gloss (a «vidrio pulido» is glass), metal before gloss.
_WORDS = (
    # Short words must end there: «mar» is the sea, «Marble» is stone.
    ("water", r"agua|water|pool|piscina|pileta|espejo[_ ]de[_ ]agua|"
              r"(?:lago|lake|mar|sea)(?![a-záéíóúñç])|ocean|oc[eé]ano|[aá]gua"),
    ("glass", r"vidri|glass|cristal|crystal|vitral|window[_ ]?pane|"
              r"mampara|vidro"),
    ("metal", r"metal|acero|steel|a[cç]o(?![a-záéíóúñç])|alumin|hierro|"
              r"iron|ferro|chrom|"
              r"cromo|inox|bronce|bronze|cobre|copper|lat[oó]n|brass|"
              r"galvaniz|zinc|zinco|titani"),
    ("gloss", r"m[aá]rmol|marble|m[aá]rmore|granit|porcel|cer[aá]mic|"
              r"ceramic|azulej|tile|lacad|lacquer|laca|pulid|polish|"
              r"brillant|glossy|pl[aá]stic|acr[ií]lic|esmalt|enamel|"
              r"lacquered|gleam"),
    ("satin", r"madera|wood|madeira|parquet|parquet|laminad|laminate|"
              r"cuero|leather|couro|satin|satinad|semi"),
)
_RX = [(f, re.compile(r"(?:^|[^a-záéíóúñç])(?:" + pat + r")", re.IGNORECASE))
       for f, pat in _WORDS]


#: A RAL Classic colour ("RAL 1018 Amarillo zinc"): a PAINT, whatever its
#: name says. 29 of the palette's names read as something else to the
#: keywords — «Amarillo zinc», «Azul acero», «Gris hierro», «Pardo cobre»,
#: «Aluminio blanco» as polished metal, «Azul agua» / «Azul océano» as water,
#: «brillante» (RAL's *luminous*, a fluorescent paint) as gloss — and a wall
#: painted RAL 1018 rendered as a mirror-polished metal, its reflections
#: smearing dark bands into every corner (EEVEE and Cycles).
_RAL = re.compile(r"^\s*RAL[\s_-]*\d{3,4}(?!\d)", re.IGNORECASE)


#: The material library's CATEGORY decides first (the user's choice aside):
#: a name alone misleads — RAL «Amarillo zinc» read as metal, the wall tile
#: «Swimming pool ceramic» as water, the floor «Woven sea rush» as water —
#: and library names are translated, so the same texture guessed one way in
#: English and another in Spanish. The category does not move.
#:
#: Categories that ARE a material type fix the finish outright.
CATEGORY_FINISH = {
    "colors": "matte",          # paint (RAL Classic)
    "brick": "matte", "concrete": "matte", "stone": "matte",
    "ground": "matte", "fabric": "matte", "wallpaper": "matte",
    "rug": "matte", "sky": "matte", "roof": "matte",
    "wood": "satin",
    "metal": "metal", "glass": "glass", "water": "water",
}
#: Categories that say WHERE a material goes (a wall, a floor) mix
#: materials, so the name decides — but never a finish that cannot be that
#: surface: no wall or floor is water or clear glass (a «glass mosaic» tile
#: is a glossy tile).
CATEGORY_EXCLUDES = {
    "wall": {"water", "glass"},
    "floor": {"water", "glass"},
}
#: An excluded finish the name asked for: glass on a wall or floor is a
#: glass MOSAIC — glossy and opaque; water is a misread word («Swimming pool
#: ceramic», «Woven sea rush»), so the rest of the name decides instead.
_EXCLUDED_AS = {"glass": "gloss"}

_HASHED = re.compile(r"^[0-9a-f]{16}-", re.IGNORECASE)
_LIBRARY: dict | None = None


def _library_index() -> dict:
    """``{key: category}`` from the bundled texture library, keyed by the
    picture's file name (``old_wood.jpg``) and by the item's English name,
    lower-cased — how a document made before materials carried their
    category still finds it. Built once."""
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = {}
        try:
            import json
            from pathlib import Path
            path = (Path(__file__).resolve().parents[1] / "resources"
                    / "textures" / "library.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            for cat in data.get("categories", []):
                for item in cat.get("items", []):
                    for key in (Path(item.get("file", "")).name,
                                item.get("name", "")):
                        if key:
                            _LIBRARY.setdefault(key.lower(), cat["id"])
        except Exception:  # noqa: BLE001 — no library: names decide
            pass
    return _LIBRARY


def category_of(name: str | None, picture: str | None = None):
    """The library category a material comes from — a RAL name is a
    colour; otherwise its picture's file (the document's embedded copy is
    ``<hash>-<file>``) or its name in the bundled library. ``None`` when it
    is not a library material."""
    if name and _RAL.match(str(name)):
        return "colors"
    index = _library_index()
    if picture:
        from pathlib import Path
        base = _HASHED.sub("", Path(str(picture)).name).lower()
        if base in index:
            return index[base]
    if name and str(name).lower() in index:
        return index[str(name).lower()]
    return None


def _by_name(name, picture, exclude=()) -> Optional[str]:
    for text in (name, picture):
        if not text:
            continue
        low = str(text).replace("-", "_")
        for finish, rx in _RX:
            if rx.search(low):
                if finish not in exclude:
                    return finish
                if finish in _EXCLUDED_AS:
                    return _EXCLUDED_AS[finish]
                # otherwise: keep reading the name for another finish
    return None


def guess(name: str | None, picture: str | None = None,
          opacity: float | None = None,
          category: str | None = None) -> str:
    """The finish a material most likely has: from its library CATEGORY
    (given, or looked up), then from what it is called."""
    translucent = opacity is not None and opacity < 0.999
    cat = category or category_of(name, picture)
    fixed = CATEGORY_FINISH.get(cat)
    if fixed is not None:
        # A see-through paint is glass; any other category keeps its own.
        if translucent and fixed == "matte" and cat == "colors":
            return "glass"
        return fixed
    named = _by_name(name, picture, CATEGORY_EXCLUDES.get(cat, ()))
    if named is not None:
        return named
    return "glass" if translucent else "matte"


def resolve(chosen: Optional[str], name: str | None,
            picture: str | None = None,
            opacity: float | None = None,
            category: str | None = None) -> str:
    """The finish to use: the user's choice, or the guess (category first)
    when it is ``auto``/absent/unknown."""
    if chosen in FINISHES:
        return chosen
    return guess(name, picture, opacity, category)
