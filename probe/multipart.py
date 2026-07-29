"""Python port of the extension's Coinbase multipart/@defer folding.

Mirror of scraper-browser-extensions/src/platforms/coinbase/multipart.ts.
Coinbase serves GraphQL as `multipart/mixed; deferSpec=...` with `--graphql`
boundaries: a base payload plus `incremental` patches that fill `@defer`-red
fields (balances). Fold every patch into the base so a present-but-empty field
stays present and "still streaming" is never mistaken for "empty".
"""

from __future__ import annotations

import json
from typing import Any

# Keys that would let a crafted response walk up the prototype chain.
_FORBIDDEN_KEYS = {"__proto__", "constructor", "prototype"}


def fold_multipart(text: str) -> dict[str, Any]:
    """Parse a (possibly multipart) GraphQL body into a single folded object.

    Falls back to plain json.loads when there are no `--graphql` boundaries.
    """
    parts = text.split("--graphql")
    json_objects: list[dict[str, Any]] = []

    for part in parts:
        start = part.find("{")
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(part)):
            ch = part[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth == 0:
                try:
                    json_objects.append(json.loads(part[start : i + 1]))
                except json.JSONDecodeError:
                    pass  # skip malformed chunk
                break

    if not json_objects:
        return json.loads(text)

    base = json_objects[0]
    for chunk in json_objects[1:]:
        _apply_incremental_patches(base, chunk)
    base.pop("incremental", None)
    base.pop("hasNext", None)
    return base


def _apply_incremental_patches(base: dict[str, Any], chunk: dict[str, Any]) -> None:
    incremental = chunk.get("incremental")
    if not isinstance(incremental, list):
        return
    for patch in incremental:
        if not isinstance(patch, dict):
            continue
        data = patch.get("data")
        path = patch.get("path")
        if data is None or not isinstance(path, list):
            continue
        if any(str(seg) in _FORBIDDEN_KEYS for seg in path):
            continue
        target: Any = base
        for seg in path:
            if target is None:
                break
            try:
                target = target[seg]
            except (KeyError, IndexError, TypeError):
                target = None
                break
        if isinstance(target, dict) and isinstance(data, dict):
            _deep_merge(target, data)


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, s_val in source.items():
        if key in _FORBIDDEN_KEYS:
            continue
        if s_val is None:
            continue
        t_val = target.get(key)
        if isinstance(s_val, dict) and isinstance(t_val, dict):
            _deep_merge(t_val, s_val)
        else:
            target[key] = s_val
