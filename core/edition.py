# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Who this build is: the 3djlchb edition, installed beside the official one.

Qt files the preferences (the registry on Windows) and the per-user data
(autosaves, scale figures, sheet templates) under the organization name.
The official IngeTrazo uses «IngeTrazo»; with the same name both builds
would read and write one store, and the layouts, toolbars and panels of
this edition would show up in the official one. Logs, the extensions
folder and the texture and render caches stay shared.

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
