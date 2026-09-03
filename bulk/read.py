"""Reading the bulk CSV files.

These are PostgreSQL `COPY TO` output: CSV, UTF-8, header row, `ESCAPE '\\'`.
That escape setting matters. PostgreSQL's default CSV quoting doubles an
embedded quote; with `ESCAPE '\\'` it backslash-escapes instead, so a reader
configured for the default will mis-split rows containing quotes. Python's
csv module handles it with `doublequote=False, escapechar='\\'`.

Rows are counted with a real CSV reader rather than by counting newlines,
because text fields legitimately contain them.

DuckDB reads gzip and zstd but not bzip2, so files are streamed through
Python's bz2 decompressor rather than handed over as archives.
"""

from __future__ import annotations

import bz2
import csv
import io
from contextlib import contextmanager
from pathlib import Path

#: PostgreSQL COPY ... WITH (FORMAT csv, ENCODING utf8, ESCAPE '\', HEADER)
DIALECT = {
    "delimiter": ",",
    "quotechar": '"',
    "doublequote": False,
    "escapechar": "\\",
    "lineterminator": "\n",
}

# Some fields (opinion text, headmatter) exceed the default 128 KiB limit.
csv.field_size_limit(1024 * 1024 * 64)


@contextmanager
def open_text(path: Path, encoding: str = "utf-8"):
    """A text stream over a bulk file, decompressing bz2 on the fly."""
    path = Path(path)
    raw = bz2.open(path, "rb") if path.suffix == ".bz2" else open(path, "rb")
    try:
        yield io.TextIOWrapper(raw, encoding=encoding, newline="")
    finally:
        raw.close()


@contextmanager
def open_rows(path: Path):
    """Yield (header, row iterator) for a bulk CSV file."""
    with open_text(path) as stream:
        reader = csv.reader(stream, **DIALECT)
        try:
            header = next(reader)
        except StopIteration:
            header = []
        yield header, reader


def count_rows(path: Path, on_progress=None, every: int = 1_000_000) -> tuple:
    """Count data rows, excluding the header. Returns (header, rows)."""
    with open_rows(path) as (header, reader):
        rows = 0
        for _ in reader:
            rows += 1
            if on_progress and rows % every == 0:
                on_progress(rows)
    return header, rows


def read_dicts(path: Path, columns=None):
    """Stream rows as dicts, optionally keeping only some columns."""
    with open_rows(path) as (header, reader):
        index = {name: i for i, name in enumerate(header)}
        keep = list(columns) if columns else header
        missing = [c for c in keep if c not in index]
        if missing:
            raise KeyError(f"{Path(path).name}: no such column(s): {', '.join(missing)}")
        positions = [(name, index[name]) for name in keep]
        for row in reader:
            if len(row) < len(header):
                continue
            yield {name: row[position] for name, position in positions}
