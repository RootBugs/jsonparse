"""JSON Lines (NDJSON) streaming reader.

Each physical line of the source is one complete JSON document. Blank and
whitespace-only lines are skipped. Parse errors are re-raised with the
record number attached: ``exc.record`` says which line of the stream
failed, while ``line``/``column`` stay relative to that record's own text,
and the message gains a ``[JSON Lines record N]`` suffix.
"""

from __future__ import annotations

from .parser import JSONError, loads

__all__ = ["iter_json_lines", "loads_lines"]


def iter_json_lines(lines):
    """Yield one parsed value per non-blank line of ``lines``.

    ``lines`` is any iterable of strings — a file object (iterates lines),
    a list of records, or a whole JSON Lines ``str`` (split into lines
    automatically). The reader is lazy: it never holds more than one
    record in memory, and a bad record raises immediately at its
    position in the stream.
    """
    if isinstance(lines, str):
        lines = lines.splitlines()
    for record, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            yield loads(line)
        except JSONError as exc:
            exc.record = record
            exc.args = (f"{exc.args[0]} [JSON Lines record {record}]",)
            raise


def loads_lines(text):
    """Parse a whole JSON Lines document (convenience over the iterator)."""
    return list(iter_json_lines(text.splitlines()))
