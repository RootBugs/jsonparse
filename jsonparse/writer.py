"""JSON serializer: pretty (indent), compact, key-sorted and canonical modes.

Strictness choices (documented):

* Output is always RFC 8259: ``NaN``/``Infinity`` raise :class:`ValueError`
  instead of emitting unquoted tokens.
* Container cycles raise :class:`ValueError` (shared non-circular
  references are fine — only a cycle through the *current path* errors).
* Object keys must be ``str`` (``TypeError`` otherwise) — coercing ``1``
  to ``"1"`` silently collides with an existing key.
* Floats print via :func:`repr`, the shortest form that round-trips
  exactly; this is what :func:`canonical` means by "minimal float repr".
"""

from __future__ import annotations

import math

__all__ = ["canonical", "dump", "dumps"]

_COMPACT = (",", ":")
_PRETTY = (", ", ": ")
_INDENTED = (",", ": ")  # Python-style item separator when indenting


def dumps(obj, indent=None, sort_keys=False, compact=False, ensure_ascii=False):
    """Serialize ``obj`` to a JSON string.

    Args:
        indent: ``None`` for single-line output, or a non-negative int —
            Python-style nesting (``compact`` is ignored when set).
        sort_keys: sort object keys at every level.
        compact: no insignificant spaces (ignored when ``indent`` is set).
        ensure_ascii: escape non-ASCII as ``\\uXXXX`` (surrogate pairs for
            astral-plane characters) when True.
    """
    if indent is None:
        separators = _COMPACT if compact else _PRETTY
        return "".join(
            _serialize(obj, sort_keys, ensure_ascii, separators, None, 0, set())
        )
    if isinstance(indent, bool) or not isinstance(indent, int) or indent < 0:
        raise TypeError("indent must be None or a non-negative int")
    return "".join(
        _serialize(obj, sort_keys, ensure_ascii, _INDENTED, indent, 0, set())
    )


def dump(obj, stream, indent=None, sort_keys=False, compact=False,
         ensure_ascii=False):
    """Serialize ``obj`` and write it to ``stream`` (no trailing newline)."""
    stream.write(
        dumps(obj, indent=indent, sort_keys=sort_keys, compact=compact,
              ensure_ascii=ensure_ascii)
    )


def canonical(obj):
    """Canonical JSON: sorted keys, compact separators, minimal float repr.

    Canonical form is stable under round-tripping:
    ``canonical(loads(canonical(x))) == canonical(x)`` for any
    serializable ``x``.
    """
    return dumps(obj, sort_keys=True, compact=True, ensure_ascii=False)


# --------------------------------------------------------------- internals --
def _serialize(obj, sort_keys, ensure_ascii, separators, width, depth, active):
    if isinstance(obj, str):
        return [_quote(obj, ensure_ascii)]
    if obj is True:
        return ["true"]
    if obj is False:
        return ["false"]
    if obj is None:
        return ["null"]
    if isinstance(obj, int):
        return [str(obj)]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(
                f"{obj!r} is not a finite number; NaN and Infinity are "
                "not valid JSON"
            )
        return [repr(obj)]
    if isinstance(obj, (dict, list, tuple)):
        marker = id(obj)
        if marker in active:
            raise ValueError(
                f"Circular reference detected in {type(obj).__name__} "
                "at serialization time"
            )
        active.add(marker)
        try:
            if isinstance(obj, dict):
                return _object(obj, sort_keys, ensure_ascii, separators,
                               width, depth, active)
            return _array(obj, sort_keys, ensure_ascii, separators, width,
                          depth, active)
        finally:
            active.discard(marker)
    raise TypeError(
        f"Object of type {type(obj).__name__} is not JSON serializable"
    )


def _line(width, depth):
    return "" if width is None else "\n" + " " * (width * depth)


def _array(items, sort_keys, ensure_ascii, separators, width, depth, active):
    if not items:
        return ["[]"]
    parts = ["["]
    for index, item in enumerate(items):
        if index:
            parts.append(separators[0])
        parts.append(_line(width, depth + 1))
        parts.extend(
            _serialize(item, sort_keys, ensure_ascii, separators, width,
                       depth + 1, active)
        )
    parts.append(_line(width, depth))
    parts.append("]")
    return parts


def _object(mapping, sort_keys, ensure_ascii, separators, width, depth,
             active):
    if not mapping:
        return ["{}"]
    for key in mapping:
        if not isinstance(key, str):
            raise TypeError(
                "JSON object keys must be strings, got "
                f"{type(key).__name__}"
            )
    keys = sorted(mapping) if sort_keys else list(mapping)
    parts = ["{"]
    for index, key in enumerate(keys):
        if index:
            parts.append(separators[0])
        parts.append(_line(width, depth + 1))
        parts.append(_quote(key, ensure_ascii))
        parts.append(separators[1])
        parts.extend(
            _serialize(mapping[key], sort_keys, ensure_ascii, separators,
                       width, depth + 1, active)
        )
    parts.append(_line(width, depth))
    parts.append("}")
    return parts


_STRING_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _quote(text, ensure_ascii):
    chunks = ['"']
    for char in text:
        if char in _STRING_ESCAPES:
            chunks.append(_STRING_ESCAPES[char])
        elif ord(char) < 0x20:
            chunks.append(f"\\u{ord(char):04x}")
        elif ensure_ascii and ord(char) > 0x7E:
            code = ord(char)
            if code > 0xFFFF:  # astral plane: surrogate pair
                code -= 0x10000
                high = 0xD800 + (code >> 10)
                low = 0xDC00 + (code & 0x3FF)
                chunks.append(f"\\u{high:04x}\\u{low:04x}")
            else:
                chunks.append(f"\\u{code:04x}")
        else:
            chunks.append(char)
    chunks.append('"')
    return "".join(chunks)
