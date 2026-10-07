# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 IngeTrazo contributors.
"""How Blender samples an animation's frames — one set of rules for the
panel and the render, testable without Qt or Blender.

Two ways (``processing``):

* **native** — the Render tab's own quality (Draft / Medium / High) and
  the samples IngeTrazo gives it (``core.render_blender.SAMPLES``);
* **custom** — the animation's own numbers, per engine: EEVEE has samples
  only; Cycles has adaptive sampling (noise threshold, max and min
  samples) or fixed samples, and a time limit.

The custom settings reach Blender as the flat keys of a serve request —
``samples``, ``noise_threshold`` (``None`` = adaptive off), ``min_samples``,
``time_limit`` — which ``resources/blender/render_scene.py`` applies only
when present (the viewport's Sync never sends them).
"""
from __future__ import annotations

ENGINE_EEVEE = "eevee"
ENGINE_CYCLES = "cycles"
PROCESSING_NATIVE = "native"
PROCESSING_CUSTOM = "custom"

DEFAULT_SAMPLES = 128
DEFAULT_MAX_SAMPLES = 128
DEFAULT_MIN_SAMPLES = 0
DEFAULT_NOISE_THRESHOLD = 0.0100
DEFAULT_TIME_LIMIT = 0.0
#: The Render tab's quality, as it stores it (QSettings).
RENDER_QUALITY_KEY = "render/quality"
#: ``core.render_blender.SAMPLES``, should it not be importable.
_FALLBACK_SAMPLES = {"eevee": (16, 64, 128), "cycles": (32, 128, 512)}


def _int(value, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(float(value)))
    except (TypeError, ValueError):
        return max(minimum, int(default))


def _float(value, default: float) -> float:
    try:
        return max(0.0, float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return max(0.0, float(default))


def normalize_sampling(engine: str, processing: str, values: dict | None = None) -> dict:
    """The stored / typed values made safe, without changing their meaning."""
    values = values if isinstance(values, dict) else {}
    engine = engine if engine in (ENGINE_EEVEE, ENGINE_CYCLES) else ENGINE_CYCLES
    processing = (processing if processing in (PROCESSING_NATIVE, PROCESSING_CUSTOM)
                  else PROCESSING_NATIVE)
    out = {
        "processing": processing,
        "engine": engine,
        "samples": _int(values.get("samples"), DEFAULT_SAMPLES, 1),
        "noise_threshold_enabled": bool(values.get("noise_threshold_enabled", True)),
        "noise_threshold": _float(values.get("noise_threshold"), DEFAULT_NOISE_THRESHOLD),
        "max_samples": _int(values.get("max_samples"), DEFAULT_MAX_SAMPLES, 1),
        "min_samples": _int(values.get("min_samples"), DEFAULT_MIN_SAMPLES, 0),
        "time_limit": _float(values.get("time_limit"), DEFAULT_TIME_LIMIT),
    }
    out["min_samples"] = min(out["min_samples"], out["max_samples"])
    return out


def native_samples(engine: str, quality: int) -> int:
    """The samples IngeTrazo's Render tab uses for ``quality`` (0–2)."""
    quality = max(0, min(2, int(quality)))
    try:
        from core.render_blender import SAMPLES
        return int(SAMPLES[engine][quality])
    except (ImportError, KeyError, IndexError, TypeError, ValueError):
        return _FALLBACK_SAMPLES.get(engine, _FALLBACK_SAMPLES[ENGINE_CYCLES])[quality]


def request_fields(engine: str, processing: str, values: dict | None = None,
                   native_quality: int = 1) -> dict:
    """The keys one frame's serve request carries.

    Native: the Render tab's sample count for its quality, nothing else —
    as Render itself renders. Custom: per engine (see the module)."""
    cfg = normalize_sampling(engine, processing, values)
    if cfg["processing"] == PROCESSING_NATIVE:
        return {"samples": native_samples(cfg["engine"], native_quality)}
    if cfg["engine"] == ENGINE_EEVEE:
        return {"samples": cfg["samples"]}
    if cfg["noise_threshold_enabled"]:
        return {"samples": cfg["max_samples"],
                "noise_threshold": cfg["noise_threshold"],
                "min_samples": cfg["min_samples"],
                "time_limit": cfg["time_limit"]}
    return {"samples": cfg["samples"], "noise_threshold": None,
            "time_limit": cfg["time_limit"]}


def visible_custom_controls(engine: str, noise_enabled: bool) -> dict:
    """Which custom controls apply to ``engine``."""
    cycles = engine == ENGINE_CYCLES
    return {
        "noise_threshold": cycles,
        "adaptive_samples": cycles and bool(noise_enabled),
        "fixed_samples": engine == ENGINE_EEVEE or (cycles and not bool(noise_enabled)),
        "time_limit": cycles,
    }


def describe(engine: str, fields: dict) -> str:
    """One line for the console: what Blender was asked for."""
    from core.i18n import tr
    name = "EEVEE" if engine == ENGINE_EEVEE else "Cycles"
    if "noise_threshold" not in fields:
        return tr("{engine} · {n} samples", engine=name, n=int(fields.get("samples", 0)))
    if fields.get("noise_threshold") is None:
        return tr("{engine} · noise threshold off · {n} samples · time limit {t:g} s",
                  engine=name, n=int(fields.get("samples", 0)),
                  t=float(fields.get("time_limit", 0.0)))
    return tr("{engine} · noise threshold {thr:g} · {lo}–{hi} samples · time limit {t:g} s",
              engine=name, thr=float(fields["noise_threshold"]),
              lo=int(fields.get("min_samples", 0)), hi=int(fields.get("samples", 0)),
              t=float(fields.get("time_limit", 0.0)))
