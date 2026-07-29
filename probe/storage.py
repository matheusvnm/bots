"""Cookie + web-storage + IndexedDB snapshotter for the probe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Reads localStorage + sessionStorage as plain dicts.
STORAGE_JS = """
() => {
  const dump = (s) => {
    const o = {};
    for (let i = 0; i < s.length; i++) { const k = s.key(i); o[k] = s.getItem(k); }
    return o;
  };
  return { local: dump(window.localStorage), session: dump(window.sessionStorage) };
}
"""

# Enumerates IndexedDB database names + object-store names (best effort).
INDEXEDDB_JS = """
async () => {
  if (!window.indexedDB || !indexedDB.databases) return [];
  const dbs = await indexedDB.databases();
  const out = [];
  for (const meta of dbs) {
    if (!meta.name) continue;
    const stores = await new Promise((resolve) => {
      const req = indexedDB.open(meta.name);
      req.onsuccess = () => {
        const db = req.result;
        const names = Array.from(db.objectStoreNames);
        db.close();
        resolve(names);
      };
      req.onerror = () => resolve([]);
    });
    out.push({ name: meta.name, version: meta.version, stores });
  }
  return out;
}
"""


class StateSnapshotter:
    def __init__(self, log_dir: Path, context: Any):
        self.dir = log_dir / "storage"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.context = context

    def _write(self, seq: int, name: str, data: Any) -> None:
        path = self.dir / f"{seq:04d}_{name}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def snapshot(self, page: Any, seq: int) -> None:
        try:
            self._write(seq, "cookies", self.context.cookies())
        except Exception:  # noqa: BLE001
            self._write(seq, "cookies", {"error": "cookies() failed"})

        try:
            storage = page.evaluate(STORAGE_JS)
        except Exception as exc:  # noqa: BLE001
            storage = {"local": {"error": str(exc)}, "session": {}}
        self._write(seq, "local", storage.get("local", {}))
        self._write(seq, "session", storage.get("session", {}))

        try:
            idb = page.evaluate(INDEXEDDB_JS)
        except Exception as exc:  # noqa: BLE001
            idb = {"error": str(exc)}
        self._write(seq, "indexeddb", idb)
