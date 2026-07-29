"""Monotonic seq counter + run manifest for artifact correlation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SeqClock:
    def __init__(self, meta: dict[str, Any]):
        self._n = 0
        self.meta = meta
        self.index: list[dict[str, Any]] = []
        self.started_at = _now_iso()

    def next(self) -> int:
        s = self._n
        self._n += 1
        return s

    def record(self, seq: int, *, kind: str, url: str, label: str | None = None) -> None:
        self.index.append(
            {"seq": seq, "ts": _now_iso(), "kind": kind, "url": url, "label": label}
        )

    def write_manifest(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": self.meta,
            "started_at": self.started_at,
            "ended_at": _now_iso(),
            "index": self.index,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
