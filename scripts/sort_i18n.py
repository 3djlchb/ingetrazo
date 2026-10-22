# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Sort the i18n dictionaries by their English key, alphabetically.

    python scripts/sort_i18n.py              # sort every i18n/*.json
    python scripts/sort_i18n.py --check      # exit 1 if one is unsorted
    python scripts/sort_i18n.py --merge REF  # union with REF's copies first

The order ignores case (``casefold``), so «Area» and «area…» sit together;
equal keys under that rule keep a stable order by the raw key. Each file
keeps its own layout (one-space indent, real UTF-8, its line endings), so
a sorted file differs from the unsorted one only in the order of its lines.

``--merge REF`` is for after a merge from upstream: upstream appends new
strings at the end, so a sorted dictionary conflicts as a whole. It takes
the key union of the working copy and ``REF``'s copy (``git show``) —
``REF`` wins where both translate the same key differently — and sorts
the result. Resolve the conflict this way, then ``git add i18n``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "i18n"


def sort_key(key: str) -> tuple:
    return (key.casefold(), key)


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def dump(data: dict, newline: str = "\n") -> str:
    out = json.dumps(data, indent=1, ensure_ascii=False)
    return out.replace("\n", newline) + newline


def sorted_dict(data: dict) -> dict:
    return {k: data[k] for k in sorted(data, key=sort_key)}


def _ref_copy(ref: str, path: Path) -> dict | None:
    rel = path.relative_to(ROOT).as_posix()
    try:
        raw = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        return None
    return json.loads(raw.decode("utf-8"))


def _working_copy(text: str) -> dict:
    """The working copy as a dict; a file left with conflict markers is
    read side by side (ours, then theirs) so no key is lost."""
    if "<<<<<<< " not in text:
        return json.loads(text)
    merged: dict = {}
    for side in (0, 1):
        lines, state = [], None
        for line in text.splitlines():
            if line.startswith("<<<<<<< "):
                state = 0
            elif line.startswith("=======") and state == 0:
                state = 1
            elif line.startswith(">>>>>>> "):
                state = None
            elif state is None or state == side:
                lines.append(line)
        body = "\n".join(lines)
        # One side may end without the comma the other side's lines carry.
        body = body.replace('"\n }', '"\n}').rstrip()
        try:
            part = json.loads(body)
        except json.JSONDecodeError:
            part = json.loads(_fix_commas(lines))
        for k, v in part.items():
            merged.setdefault(k, v)
    return merged


def _fix_commas(lines: list) -> str:
    rows = [ln.strip().rstrip(",") for ln in lines
            if ln.strip() not in ("{", "}", "")]
    return "{" + ",".join(rows) + "}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="only report unsorted files (exit 1 if any)")
    ap.add_argument("--merge", metavar="REF",
                    help="union with REF's copy before sorting")
    args = ap.parse_args(argv)
    unsorted = []
    for path in sorted(I18N.glob("*.json")):
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()                  # line endings as they are
        nl = _newline(text)
        data = _working_copy(text)
        if args.merge:
            theirs = _ref_copy(args.merge, path)
            if theirs is not None:
                data = {**data, **theirs}
        if (not args.merge and "<<<<<<< " not in text
                and list(data) == sorted(data, key=sort_key)):
            continue                          # in order: its layout stays
        result = dump(sorted_dict(data), nl)
        if result == text:
            continue
        unsorted.append(path.name)
        if not args.check:
            path.write_text(result, encoding="utf-8", newline="")
    if args.check:
        for name in unsorted:
            print(f"unsorted: {name}")
        return 1 if unsorted else 0
    for name in unsorted:
        print(f"sorted: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
