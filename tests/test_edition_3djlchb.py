# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The 3djlchb edition builds with its own plugins and installs beside the
official IngeTrazo (core/edition.py, installer/ingetrazo.iss).

An upstream merge can rewrite ingetrazo.spec or the installer script and
quietly drop what only this edition has: these tests say so before a
release is built without it.
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSettings

from core import edition

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "installer" / "ingetrazo.iss"
SPEC = ROOT / "ingetrazo.spec"
OFFICIAL_APP_ID = "63D1C88D-591C-48C8-A13B-5E41810D05E4"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8-sig")


def test_the_bundle_carries_the_edition_plugins():
    spec = SPEC.read_text(encoding="utf-8")
    for plugin in ("igz_animacion", "igz_timeline"):
        assert f"('plugins/{plugin}'," in spec, plugin
        assert (ROOT / "plugins" / plugin / "__init__.py").is_file(), plugin


def test_the_installer_is_not_the_official_one():
    script = _iss()
    app_ids = re.findall(r"^AppId=.*$", script, re.MULTILINE)
    assert len(app_ids) == 1 and OFFICIAL_APP_ID not in app_ids[0]
    assert re.search(r"^AppId=\{\{C8D6F86A-BE83-4152-A84F-89C10F4E4573\}",
                     script, re.MULTILINE)
    assert '#define MyAppName "IngeTrazo 3djlchb"' in script
    assert r"DefaultDirName={autopf}\{#MyAppName}" in script


def test_no_official_progid_is_written_or_deleted():
    # The official build owns IngeTrazo.Document / .dae / .skp: writing them
    # would point its files here, and uninstalling would delete its keys.
    registry = _iss().split("[Registry]", 1)[1].split("[Run]", 1)[0]
    assert not re.search(r"IngeTrazo\.(Document|dae|skp)", registry)
    assert "{#MyProgId}.Document" in registry


def test_double_click_stays_with_the_official_build_unless_asked():
    tasks = _iss().split("[Tasks]", 1)[1].split("[", 1)[0].replace("\\\n", " ")
    for task in ("igzdefault", "skpdefault"):
        line = next(ln for ln in tasks.splitlines() if f'"{task}"' in ln)
        assert "unchecked" in line, task


def test_the_file_version_drops_the_tag_suffix():
    # Inno's VersionInfoVersion is numeric only: a «v0.6.0-3djlchb» tag
    # stopped ISCC before this.
    script = _iss()
    assert "VersionInfoVersion={#MyAppFileVersion}" in script
    assert 'Copy(MyAppVersion, 1, MyDashPos - 1)' in script


def test_the_edition_has_its_own_preferences():
    assert edition.ORGANIZATION != edition.UPSTREAM_ORGANIZATION


def _ini(tmp_path, name, values):
    st = QSettings(str(tmp_path / f"{name}.ini"), QSettings.IniFormat)
    for key, value in values.items():
        st.setValue(key, value)
    st.sync()
    return st


def test_the_first_start_copies_the_official_preferences(tmp_path):
    theirs = _ini(tmp_path, "official", {"language": "es", "units/length": "m"})
    mine = _ini(tmp_path, "edition", {})
    edition.adopt_upstream_settings(mine, theirs)
    assert mine.value("language") == "es"
    assert mine.value("units/length") == "m"


def test_a_later_start_keeps_the_edition_own_preferences(tmp_path):
    theirs = _ini(tmp_path, "official", {"language": "es"})
    mine = _ini(tmp_path, "edition", {"language": "en"})
    edition.adopt_upstream_settings(mine, theirs)
    assert mine.value("language") == "en"
