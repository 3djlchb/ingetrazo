# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Who this build is: the 3djlchb edition, installed beside the official one.

Qt files the preferences (the registry on Windows) and the per-user data
(autosaves, scale figures, sheet templates) under the organization name.
The official IngeTrazo uses «IngeTrazo»; with the same name both builds
would read and write one store, and the layouts, toolbars and panels of
this edition would show up in the official one. Logs, the extensions
folder and the texture and render caches stay shared.
"""
from __future__ import annotations

#: The official build's organization name, where the preferences come from.
UPSTREAM_ORGANIZATION = "IngeTrazo"
ORGANIZATION = "IngeTrazo-3djlchb"
APPLICATION = "IngeTrazo"

_adopted = False


def set_identity() -> None:
    """Name the application so QSettings and QStandardPaths use this
    edition's store. Before the QApplication exists, or after."""
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.setOrganizationName(ORGANIZATION)
    QCoreApplication.setApplicationName(APPLICATION)
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
