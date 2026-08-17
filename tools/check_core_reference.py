#!/usr/bin/env python3
"""Compare protected smartimport core logic against a reference smartimport.py.

String literals are normalized so translations and user-facing wording do not
count as algorithm changes. Any control-flow, call, operator, or numeric-constant
change is reported.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path

METHODS = (
    "_score_album",
    "_candidate_pool",
    "_local_match",
    "_recording_matches_track",
    "_target_track_on_release",
    "_musicbrainz_existing_match",
    "_duplicate_reason",
    "_target_track_from_existing_release",
    "_apply_safe_disc_fallback",
    "_apply_target_track",
    "_group_key",
    "_group_is_coherent",
    "_prefer_album_check",
)


class NormalizeStrings(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value="<STR>"), node)
        return node


def method_hashes(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    plugin = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SmartImportPlugin"
    )
    result = {}
    for name in METHODS:
        method = next(
            node
            for node in plugin.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        )
        normalized = NormalizeStrings().visit(ast.fix_missing_locations(method))
        payload = ast.dump(normalized, include_attributes=False).encode()
        result[name] = hashlib.sha256(payload).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument(
        "current",
        type=Path,
        nargs="?",
        default=Path("beetsplug/smartimport.py"),
    )
    args = parser.parse_args()
    reference = method_hashes(args.reference)
    current = method_hashes(args.current)
    changed = []
    for name in METHODS:
        status = "OK" if reference[name] == current[name] else "CHANGED"
        print(f"{status:7} {name}")
        if status != "OK":
            changed.append(name)
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
