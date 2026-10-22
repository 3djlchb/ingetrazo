# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Every i18n dictionary keeps its English keys in alphabetical order, so
a string is found by reading down the file. A new string added out of
place fails here: ``python scripts/sort_i18n.py`` puts it where it goes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sort_i18n import main, sort_key, sorted_dict  # noqa: E402


def test_every_dictionary_is_in_alphabetical_order():
    for path in sorted((ROOT / "i18n").glob("*.json")):
        keys = list(json.loads(path.read_text(encoding="utf-8")))
        assert keys == sorted(keys, key=sort_key), (
            f"{path.name} is not sorted: run python scripts/sort_i18n.py")


def test_the_order_ignores_case_and_keeps_every_translation():
    data = {"zoom": "z", "Area": "A", "area of a face": "a", "Box": "B"}
    out = sorted_dict(data)
    assert list(out) == ["Area", "area of a face", "Box", "zoom"]
    assert out == data


def test_check_mode_writes_nothing(tmp_path, monkeypatch):
    import sort_i18n
    (tmp_path / "xx.json").write_text('{\n "b": "1",\n "a": "2"\n}\n',
                                      encoding="utf-8")
    monkeypatch.setattr(sort_i18n, "I18N", tmp_path)
    assert main(["--check"]) == 1
    assert list(json.loads((tmp_path / "xx.json").read_text("utf-8"))) == \
        ["b", "a"]
    assert main([]) == 0
    assert main(["--check"]) == 0


def test_a_conflicted_file_keeps_the_keys_of_both_sides(tmp_path, monkeypatch):
    import sort_i18n
    (tmp_path / "xx.json").write_text(
        '{\n<<<<<<< HEAD\n "b": "ours",\n "c": "C"\n=======\n'
        ' "b": "theirs",\n "a": "A"\n>>>>>>> upstream/main\n}\n',
        encoding="utf-8")
    monkeypatch.setattr(sort_i18n, "I18N", tmp_path)
    assert main([]) == 0
    got = json.loads((tmp_path / "xx.json").read_text("utf-8"))
    assert list(got) == ["a", "b", "c"]
