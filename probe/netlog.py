"""Structured network.jsonl writer + GraphQL operation splitter for the probe."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from probe.multipart import fold_multipart

_GRAPHQL_MARKER = "/graphql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_graphql(url: str) -> bool:
    return _GRAPHQL_MARKER in urlparse(url).path


def extract_operation_name(url: str, body: str | None) -> str | None:
    qs = parse_qs(urlparse(url).query)
    if "operationName" in qs and qs["operationName"]:
        return qs["operationName"][0]
    if body:
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(parsed, dict):
            name = parsed.get("operationName")
            return name if isinstance(name, str) else None
    return None


def build_row(
    *,
    seq: int,
    kind: str,
    method: str,
    url: str,
    status: int | None,
    request_headers: dict[str, str] | None,
    request_body: str | None,
    response_headers: dict[str, str] | None,
    response_body: str | None,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "ts": _now_iso(),
        "kind": kind,
        "method": method,
        "url": url,
        "status": status,
        "operation_name": extract_operation_name(url, request_body or response_body),
        "request_headers": request_headers,
        "request_body": request_body,
        "response_headers": response_headers,
        "response_body": response_body,
    }


class NetworkRecorder:
    """Appends structured rows to network.jsonl and folds GraphQL responses."""

    def __init__(self, log_dir: Path):
        self.jsonl_path = log_dir / "network.jsonl"
        self.gql_dir = log_dir / "graphql"
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self.gql_dir.mkdir(parents=True, exist_ok=True)
        self._gql_seq = 0

    def write_row(self, row: dict[str, Any]) -> None:
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def maybe_dump_graphql(self, url: str, body: str | None, status: int | None) -> str | None:
        """If this is a GraphQL response, write raw + folded JSON. Returns op name."""
        if not body or not is_graphql(url):
            return None
        op = extract_operation_name(url, body) or "unknown"
        self._gql_seq += 1
        prefix = self.gql_dir / f"{self._gql_seq:03d}_{op}"
        (prefix.with_suffix(".raw.txt")).write_text(body, encoding="utf-8")
        try:
            folded = fold_multipart(body)
            (prefix.with_suffix(".folded.json")).write_text(
                json.dumps(folded, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            (prefix.with_suffix(".folded.error")).write_text(
                f"could not fold/parse body: {exc}", encoding="utf-8"
            )
        return op
