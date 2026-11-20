# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Who this build is: the 3djlchb edition, installed beside the official one.

Qt files the preferences (the registry on Windows) and the per-user data
(autosaves, scale figures, sheet templates) under the organization name.
The official IngeTrazo uses «IngeTrazo»; with the same name both builds
would read and write one store, and the layouts, toolbars and panels of
this edition would show up in the official one. The folders upstream
names by hand (logs, the user's extensions, the texture and render
caches) take this edition's names too (DATA_DIR, DATA_DIR_LOWER): an
extension installed here does not appear there, and emptying the texture
cache here leaves the official one's alone. The update check asks this
fork's releases (RELEASES_REPO), not the official ones.

The same goes for what the desktop sees: the single-instance socket (a
second launch hands its file to the running window — of the OTHER build,
with one name), the Linux launcher and icon, and the window's identity
that docks and panels group by.
"""
from __future__ import annotations

import os
import sys

#: The official build's organization name, where the preferences come from.
UPSTREAM_ORGANIZATION = "IngeTrazo"
ORGANIZATION = "IngeTrazo-3djlchb"
APPLICATION = "IngeTrazo"
#: What menus and launchers call this edition.
DISPLAY_NAME = "IngeTrazo 3djlchb"
#: Desktop file id (Wayland app id), X11 instance name and icon name;
#: the official build uses «ingetrazo».
DESKTOP_ID = "ingetrazo-3djlchb"
#: The official build listens on «ingetrazo-single-instance».
SINGLE_INSTANCE_SOCKET = "ingetrazo-3djlchb-single-instance"
#: The folders upstream names by hand: «IngeTrazo» (texture and render
#: caches; logs on Windows and macOS) and «ingetrazo» (the user's
#: extensions; logs on Linux) in the official build.
DATA_DIR = ORGANIZATION
DATA_DIR_LOWER = DESKTOP_ID
#: Where the update check looks («ingelibre/ingetrazo» in the official one).
RELEASES_REPO = "3djlchb/ingetrazo"

_adopted = False


def set_identity() -> None:
    """Name the application so QSettings and QStandardPaths use this
    edition's store. Before the QApplication exists, or after."""
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.setOrganizationName(ORGANIZATION)
    QCoreApplication.setApplicationName(APPLICATION)
    if sys.platform.startswith("linux"):
        # Qt's xcb plugin takes the WM_CLASS instance from RESOURCE_NAME,
        # else from the binary's name — «ingetrazo» in both builds — and
        # X11 panels (Cinnamon, MATE) match StartupWMClass against it.
        os.environ.setdefault("RESOURCE_NAME", DESKTOP_ID)
    adopt_upstream_settings()


def adopt_upstream_settings(mine=None, theirs=None) -> None:
    """The first start of this edition copies the official preferences once,
    so the units, recent files and window layout are not lost. Never again
    after that: the two stores go their own way. ``mine``/``theirs`` are
    QSettings to use instead of the real stores (tests)."""
    global _adopted
    if mine is None:
        if _adopted:
            return
        _adopted = True
    from PySide6.QtCore import QSettings
    if mine is None:
        mine = QSettings(ORGANIZATION, APPLICATION)
    if mine.allKeys():
        return
    if theirs is None:
        theirs = QSettings(UPSTREAM_ORGANIZATION, APPLICATION)
    for key in theirs.allKeys():
        mine.setValue(key, theirs.value(key))
    mine.sync()
