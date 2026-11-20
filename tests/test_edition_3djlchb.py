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
import sys
from pathlib import Path

import pytest
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


def test_a_launch_never_hands_its_file_to_the_official_build():
    # With one socket name, opening this edition while the official one
    # runs handed the document to the official window and quit.
    assert edition.SINGLE_INSTANCE_SOCKET != "ingetrazo-single-instance"
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "SINGLE_INSTANCE_SOCKET as _SOCKET" in main_py
    assert '_SOCKET = "ingetrazo-single-instance"' not in main_py


def test_the_linux_launcher_is_not_the_official_one():
    from core import appimage
    assert appimage.DESKTOP_NAME == "ingetrazo-3djlchb.desktop"
    entry = appimage._desktop_entry(Path("/opt/IngeTrazo.AppImage"))
    assert f"Name={edition.DISPLAY_NAME}\n" in entry
    assert f"StartupWMClass={edition.DESKTOP_ID}\n" in entry
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'get("FLATPAK_ID") or DESKTOP_ID' in main_py


def test_the_linux_files_carry_the_edition_name():
    script = (ROOT / "packaging" / "build-appimage.sh").read_text(encoding="utf-8")
    assert 'NAME="IngeTrazo-3djlchb-$VERSION"' in script
    assert 'APPIMAGE="$OUT/$NAME-$ARCH.AppImage"' in script


def _deb_script() -> str:
    return (ROOT / "packaging" / "build-deb.sh").read_text(encoding="utf-8")


def test_the_deb_carries_the_edition_names():
    script = _deb_script()
    assert f"PKG={edition.DESKTOP_ID} " in script
    assert f'NAME="{edition.DISPLAY_NAME}"' in script
    for line in ("Exec=$PKG %f", "Icon=$PKG", "StartupWMClass=$PKG"):
        assert line in script, line
    # Installed under /opt/<pkg> with /usr/bin/<pkg>: never the official
    # «ingetrazo» paths.
    assert '"$STAGE/opt/$PKG"' in script and '"$STAGE/usr/bin/$PKG"' in script


def test_the_deb_depends_on_what_the_bundle_leaves_to_the_host():
    # ingetrazo.spec drops these from the bundle (_HOST_ONLY): the package
    # must make apt bring them.
    depends = next(line for line in _deb_script().splitlines()
                   if line.startswith("Depends:"))
    for pkg in ("libx11-6", "libx11-xcb1", "libxcb-glx0", "libstdc++6",
                "libgcc-s1", "libglib2.0-0", "libgl1", "libegl1"):
        assert pkg in depends, pkg


def test_the_deb_document_icons_do_not_take_the_official_names():
    script = _deb_script()
    assert 'cp "$f" "$dest/$PKG-$(basename "$f")"' in script
    assert r'<icon name=\"$PKG-' in script


def test_the_release_builds_and_installs_the_deb():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8")
    assert "packaging/build-deb.sh build/pyi/ingetrazo dist" in wf
    assert "sudo apt-get install -y ./dist/ingetrazo-3djlchb_*_amd64.deb" in wf
    assert "ingetrazo-3djlchb --check" in wf
    assert "dist/ingetrazo-3djlchb_*_amd64.deb --clobber" in wf


# --- the folders upstream names by hand ---------------------------------------

_OFFICIAL_DIR_NAMES = {"ingetrazo"}       # «IngeTrazo» and «ingetrazo», any case


def _assert_edition_folder(path, name):
    parts = [p.lower() for p in Path(path).parts]
    assert not _OFFICIAL_DIR_NAMES & set(parts), path
    assert name.lower() in parts, path


@pytest.fixture
def user_dirs(tmp_path, monkeypatch):
    for var in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.delenv("INGETRAZO_TEXTURE_CACHE", raising=False)
    return tmp_path


def test_the_texture_cache_is_not_the_official_one(user_dirs):
    # Emptying it here must leave the official build's images alone.
    from core.texture import texture_cache_root
    _assert_edition_folder(texture_cache_root(), edition.DATA_DIR)


def test_the_user_extensions_are_not_the_official_ones(user_dirs):
    # An extension installed in one build does not appear in the other;
    # the Animation extension's ffmpeg download goes beside them.
    from core.extensions import user_plugins_dir
    from plugins.igz_animacion.ffmpeg_tools import default_install_dir
    _assert_edition_folder(user_plugins_dir(), edition.DATA_DIR_LOWER)
    _assert_edition_folder(default_install_dir(), edition.DATA_DIR_LOWER)


def test_the_logs_are_not_the_official_ones(user_dirs):
    from core.paths import user_log_dir
    name = (edition.DATA_DIR_LOWER if sys.platform.startswith("linux")
            else edition.DATA_DIR)
    _assert_edition_folder(user_log_dir(), name)


@pytest.mark.parametrize("flatpak", [False, True])
def test_the_render_cache_is_not_the_official_one(user_dirs, monkeypatch,
                                                  flatpak):
    import core.render_blender as rb
    monkeypatch.setattr(rb, "in_flatpak", lambda: flatpak)
    monkeypatch.setattr(rb.Path, "home", lambda: user_dirs)
    _assert_edition_folder(rb.work_base(), edition.DATA_DIR)


def test_the_update_check_asks_this_fork_releases():
    from core import update_check as uc
    assert uc.LATEST_API == (
        "https://api.github.com/repos/3djlchb/ingetrazo/releases/latest")
    assert uc.RELEASES_PAGE == "https://github.com/3djlchb/ingetrazo/releases"
    # An answer pointing at the official releases is not followed: «new
    # version» never sends this edition's users to the official download.
    r = uc.parse_release({
        "tag_name": "v9.9.9",
        "html_url": "https://github.com/ingelibre/ingetrazo/releases/tag/v9.9.9"})
    assert r.url == uc.RELEASES_PAGE + "/latest"
