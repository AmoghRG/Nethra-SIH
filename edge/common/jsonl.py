"""JSONL reading and writing.

Jointly owned by A and B. Both track files in the pipeline are JSONL, one
object per line, because that format streams, appends, diffs, and survives a
process being killed halfway through. A single large JSON array does none of
those things.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator


def read_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """Stream records from a JSONL file.

    Blank lines and ``#`` comment lines are skipped, so a fixture can carry a
    header explaining what it is and what the expected answer is. The
    synthetic-collision fixture uses this to state its analytic TTC next to
    the data it was computed from.
    """
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "{}:{}: malformed JSON: {}".format(path, lineno, exc)
                ) from exc


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))


def write_jsonl(
    path: str | Path, records: Iterable[dict[str, Any]], header: str | None = None
) -> int:
    """Write records compactly, one per line. Returns the count written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        if header:
            for line in header.strip().splitlines():
                fh.write("# " + line + "\n")
        for rec in records:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            n += 1
    return n
