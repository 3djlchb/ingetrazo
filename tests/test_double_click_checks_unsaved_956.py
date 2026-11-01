# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Issue #956: a document handed to the running window from outside — a
double-click forwarded by the single instance, a macOS FileOpen event —
replaced the current drawing without the unsaved-changes question that
File ▸ Open and Open Recent ask first. The question now sits in
``main._open_document_in``, the seam every outside route shares (and a
cold launch, which holds nothing to lose, never sees it)."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QVector3D as V
from PySide6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])


class _GuardedWindow:
    """The shape of MainWindow the seam needs, with a scripted answer."""

    def __init__(self, answer):
        self.answer = answer
        self.opened: list[Path] = []
        self.prompts: list[str] = []
        self.file_openers: dict = {}

    def _confirm_discard(self, prompt):
        self.prompts.append(prompt)
        return self.answer

    def open_path(self, path):
        self.opened.append(path)


def test_a_refused_discard_keeps_the_current_drawing(tmp_path):
    doc = tmp_path / "other.igz"
    doc.write_text("{}")
    window = _GuardedWindow(answer=False)

    from main import _open_document_in
    _open_document_in(window, doc)

    assert window.prompts, "the unsaved-changes question was never asked"
    assert window.opened == []


def test_an_accepted_discard_opens_the_document(tmp_path):
    doc = tmp_path / "other.igz"
    doc.write_text("{}")
    window = _GuardedWindow(answer=True)

    from main import _open_document_in
    _open_document_in(window, doc)

    assert window.opened == [doc]


def test_a_window_without_the_question_still_opens(tmp_path):
    # Older/stub windows (and the macOS file-open tests' stub) have no
    # _confirm_discard: the seam must not grow a hard dependency on it.
    doc = tmp_path / "other.igz"
    doc.write_text("{}")
    window = SimpleNamespace(opened=[], file_openers={})
    window.open_path = lambda p: window.opened.append(p)

    from main import _open_document_in
    _open_document_in(window, doc)

    assert window.opened == [doc]


def test_an_import_is_not_asked_it_only_adds(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    from main import _open_document_in
    doc = tmp_path / "model.skp"
    doc.write_bytes(b"")
    calls = []
    window = SimpleNamespace(
        import_skp_path=lambda p: (calls.append(p) or True),
        _on_zoom_extents=lambda: None,
        _confirm_discard=lambda prompt: False,   # would refuse, if asked
        file_openers={})

    _open_document_in(window, doc)
    QCoreApplication.instance().processEvents()   # the import is deferred

    assert calls == [doc]


def _donor_file(tmp_path):
    """A real one-face .igz on disk, written by a throwaway window."""
    from formats import igz as igz_format
    from views.main_window import MainWindow
    donor = MainWindow()
    donor.viewport.scene.mesh.add_face(
        [V(0, 0, 0), V(2, 0, 0), V(2, 2, 0), V(0, 2, 0)])
    path = tmp_path / "donor.igz"
    igz_format.save_scene(donor.viewport.scene, path)
    donor._saved_version = donor.viewport.scene.version
    donor.close()
    return path


def _dirty_window():
    """A window holding one unsaved face of its own."""
    from views.main_window import MainWindow
    win = MainWindow()
    win.viewport.scene.mesh.add_face(
        [V(9, 9, 9), V(11, 9, 9), V(11, 11, 9), V(9, 11, 9)])
    win.viewport.scene.version += 1      # the edit, as a command records it
    assert win._is_dirty()
    return win


def test_cancelling_the_question_keeps_the_unsaved_drawing(
        tmp_path, monkeypatch):
    from main import _open_document_in
    doc = _donor_file(tmp_path)
    win = _dirty_window()
    try:
        monkeypatch.setattr(QMessageBox, "exec",
                            lambda box: QMessageBox.Cancel)
        _open_document_in(win, doc)
        # Still the window's own unsaved face, still no file behind it.
        assert win._current_path is None
        faces = list(win.viewport.scene.mesh.faces)
        assert len(faces) == 1
        assert faces[0].vertices[0].z() == 9.0
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()


def test_discarding_opens_the_double_clicked_drawing(
        tmp_path, monkeypatch):
    from main import _open_document_in
    doc = _donor_file(tmp_path)
    win = _dirty_window()
    try:
        monkeypatch.setattr(QMessageBox, "exec",
                            lambda box: QMessageBox.Discard)
        _open_document_in(win, doc)
        assert win._current_path == doc
        faces = list(win.viewport.scene.mesh.faces)
        assert len(faces) == 1
        assert faces[0].vertices[0].z() == 0.0    # the donor's face now
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()


def test_a_clean_window_is_never_asked(tmp_path, monkeypatch):
    from main import _open_document_in
    from views.main_window import MainWindow
    doc = _donor_file(tmp_path)
    win = MainWindow()                            # untouched: nothing to lose
    try:
        asked = []
        monkeypatch.setattr(QMessageBox, "exec",
                            lambda box: asked.append(box))
        _open_document_in(win, doc)
        assert asked == []
        assert win._current_path == doc
    finally:
        win._saved_version = win.viewport.scene.version
        win.close()
