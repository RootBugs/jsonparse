"""Strict RFC 8259 JSON parser with precise, caret-annotated errors.

Design notes:

* The scanner is hand-written (no regex): the number grammar, string
  escapes and surrogate pairing are validated character by character, so
  every rejection can point at the exact offending line/column.
* ``JSONError`` is a :class:`ValueError` (mirroring :mod:`json`) carrying
  ``position``/``line``/``column``/``expected``/``found`` plus a
  ``context()`` caret renderer.
* Whitespace is exactly the four RFC characters (space, tab, LF, CR);
  a leading UTF-8 BOM is tolerated for Windows interop and is not content.
* Duplicate object keys: **last occurrence wins** (documented policy, same
  as :mod:`json`).
* Numbers with a fraction or exponent become ``float`` (C double, same as
  :mod:`json`); integers become ``int`` with arbitrary precision, so big
  integers are exact. ``1e400`` parses to ``inf`` exactly like :mod:`json`
  (serialization rejects non-finite floats separately).
* ``NaN``/``Infinity``/``-Infinity`` are rejected with a dedicated message;
  they are not JSON numbers.
"""

from __future__ import annotations

__all__ = ["JSONError", "load", "loads"]

WHITESPACE = " \t\n\r"

_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class JSONError(ValueError):
    """A JSON parse error with position, expected set and caret context.

    Attributes:
        position: 0-based character offset of the error.
        line: 1-based line number.
        column: 1-based column number.
        expected: frozenset of expected-token descriptions (may be empty
            when a ``detail`` message is given instead).
        found: what was actually there (``"end of input"`` at EOF).
        text: the input, for caret rendering.
        detail: optional explicit message (overrides the expected phrase).
        record: JSON Lines record number, set by :mod:`jsonparse.jsonlines`.
    """

    def __init__(self, text, position, expected=frozenset(), detail=None):
        self.position = position
        self.expected = frozenset(expected)
        self.detail = detail
        self.text = text
        self.line = text.count("\n", 0, position) + 1
        last_newline = text.rfind("\n", 0, position)
        self.column = position - last_newline
        self.found = text[position] if position < len(text) else "end of input"
        self.record = None
        super().__init__(f"line {self.line}, column {self.column}: {self._body()}")

    def _body(self):
        if self.detail is not None:
            return self.detail
        items = sorted(self.expected)
        if len(items) == 1:
            wanted = f"expected {items[0]}"
        else:
            wanted = "expected one of " + ", ".join(items)
        return f"{wanted}; found {self.found!r}"

    def context(self):
        """The offending line plus a caret pointing at the column."""
        lines = self.text.splitlines() or [""]
        source_line = lines[self.line - 1] if self.line - 1 < len(lines) else ""
        caret = " " * (self.column - 1) + "^"
        return f"{source_line}\n{caret}"

    def __str__(self):
        return f"{self.args[0]}\n{self.context()}"


class _Parser:
    def __init__(self, text):
        if text.startswith("\ufeff"):
            text = text[1:]  # Windows interop: strip the UTF-8 BOM
        self.text = text
        self.pos = 0

    # -- helpers ---------------------------------------------------------
    def fail(self, position, expected=frozenset(), detail=None):
        raise JSONError(self.text, position, expected, detail)

    def skip_ws(self):
        while self.pos < len(self.text) and self.text[self.pos] in WHITESPACE:
            self.pos += 1

    def peek(self):
        return self.text[self.pos] if self.pos < len(self.text) else None

    # -- entry -----------------------------------------------------------
    def parse_document(self):
        value = self.read_value()
        self.skip_ws()
        if self.pos < len(self.text):
            self.fail(self.pos, {"end of input"})
        return value

    # -- values ----------------------------------------------------------
    def read_value(self):
        self.skip_ws()
        char = self.peek()
        if char is None:
            self.fail(self.pos, {"value"})
        if char == "{":
            return self.read_object()
        if char == "[":
            return self.read_array()
        if char == '"':
            return self.read_string()
        if char == "-" or char.isdigit() and char.isascii():
            if self.text.startswith("-Infinity", self.pos):
                self.fail(
                    self.pos,
                    detail="NaN and Infinity are not valid JSON numbers",
                )
            return self.read_number()
        if char in "NI":
            self.fail(
                self.pos,
                detail="NaN and Infinity are not valid JSON numbers",
            )
        if char == "t":
            return self.read_literal("true")
        if char == "f":
            return self.read_literal("false")
        if char == "n":
            return self.read_literal("null")
        self.fail(self.pos, {"value"})

    def read_literal(self, word):
        index = 0
        while index < len(word):
            if self.pos + index >= len(self.text):
                self.fail(len(self.text), {repr(word)})
            if self.text[self.pos + index] != word[index]:
                self.fail(self.pos + index, {repr(word)})
            index += 1
        self.pos += len(word)
        return {"true": True, "false": False, "null": None}[word]

    # -- numbers ---------------------------------------------------------
    def read_number(self):
        """Validate ``-?(0|[1-9][0-9]*)(.[0-9]+)?([eE][+-]?[0-9]+)?``."""
        start = self.pos
        if self.peek() == "-":
            self.pos += 1
        char = self.peek()
        if char is None or not (char.isascii() and char.isdigit()):
            self.fail(self.pos, {"digit"})
        if char == "0":
            self.pos += 1
        else:
            while True:
                char = self.peek()
                if char is not None and char.isascii() and char.isdigit():
                    self.pos += 1
                else:
                    break
        is_float = False
        if self.peek() == ".":
            is_float = True
            self.pos += 1
            char = self.peek()
            if char is None or not (char.isascii() and char.isdigit()):
                self.fail(self.pos, {"digit"})
            while True:
                char = self.peek()
                if char is not None and char.isascii() and char.isdigit():
                    self.pos += 1
                else:
                    break
        char = self.peek()
        if char in ("e", "E"):
            is_float = True
            self.pos += 1
            char = self.peek()
            if char in ("+", "-"):
                self.pos += 1
            char = self.peek()
            if char is None or not (char.isascii() and char.isdigit()):
                self.fail(self.pos, {"digit"})
            while True:
                char = self.peek()
                if char is not None and char.isascii() and char.isdigit():
                    self.pos += 1
                else:
                    break
        token = self.text[start : self.pos]
        if is_float:
            return float(token)
        return int(token)  # arbitrary precision: big integers stay exact

    # -- strings ---------------------------------------------------------
    def read_string(self):
        opening = self.pos
        self.pos += 1  # consume the opening quote
        chunks = []
        while True:
            char = self.peek()
            if char is None:
                self.fail(self.pos, {repr('"')})
            if char == '"':
                self.pos += 1
                return "".join(chunks)
            if char == "\\":
                chunks.append(self.read_escape())
                continue
            if ord(char) < 0x20:
                self.fail(
                    self.pos,
                    {r"\uXXXX-escaped control character"},
                    detail=(
                        "unescaped control character "
                        f"\\u{ord(char):04X} in string"
                    ),
                )
            chunks.append(char)
            self.pos += 1

    def read_escape(self):
        backslash = self.pos
        self.pos += 1  # the backslash itself
        char = self.peek()
        if char is None:
            self.fail(self.pos, {"valid escape"})
        if char in _ESCAPES:
            self.pos += 1
            return _ESCAPES[char]
        if char != "u":
            self.fail(self.pos, {"valid escape"})
        self.pos += 1  # the 'u' itself
        code = self.read_hex4(backslash)
        if 0xDC00 <= code <= 0xDFFF:
            self.fail(
                backslash,
                detail=f"unpaired low surrogate \\u{code:04X} in string",
            )
        if 0xD800 <= code <= 0xDBFF:
            # a high surrogate must be immediately followed by \uDC00-\uDFFF
            if not self.text.startswith("\\u", self.pos):
                self.fail(
                    self.pos, {r"low surrogate \uDC00-\uDFFF"}
                )
            pair_backslash = self.pos
            self.pos += 2
            low = self.read_hex4(pair_backslash)
            if not 0xDC00 <= low <= 0xDFFF:
                self.fail(
                    pair_backslash, {r"low surrogate \uDC00-\uDFFF"}
                )
            combined = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
            return chr(combined)
        return chr(code)

    def read_hex4(self, escape_position):
        digits = []
        for _ in range(4):
            char = self.peek()
            if char is None or char not in "0123456789abcdefABCDEF":
                self.fail(self.pos, {"hex digit"})
            digits.append(char)
            self.pos += 1
        return int("".join(digits), 16)

    # -- containers ------------------------------------------------------
    def read_array(self):
        self.pos += 1  # '['
        items = []
        self.skip_ws()
        if self.peek() == "]":
            self.pos += 1
            return items
        while True:
            items.append(self.read_value())
            self.skip_ws()
            char = self.peek()
            if char == ",":
                self.pos += 1
                continue  # a value must follow: ']' here reports "expected value"
            if char == "]":
                self.pos += 1
                return items
            self.fail(self.pos, {repr(","), repr("]")})

    def read_object(self):
        self.pos += 1  # '{'
        result = {}
        self.skip_ws()
        if self.peek() == "}":
            self.pos += 1
            return result
        while True:
            self.skip_ws()
            if self.peek() != '"':
                self.fail(self.pos, {"string key"})
            key = self.read_string()
            self.skip_ws()
            if self.peek() != ":":
                self.fail(self.pos, {repr(":")})
            self.pos += 1
            result[key] = self.read_value()  # duplicate keys: last one wins
            self.skip_ws()
            char = self.peek()
            if char == ",":
                self.pos += 1
                continue  # a key must follow: '}' here reports "expected string key"
            if char == "}":
                self.pos += 1
                return result
            self.fail(self.pos, {repr(","), repr("}")})


def loads(text):
    """Parse a complete JSON document; raise :class:`JSONError` on trouble."""
    if not isinstance(text, str):
        raise TypeError(f"loads() expects str, got {type(text).__name__}")
    return _Parser(text).parse_document()


def load(stream):
    """Read all of ``stream`` and parse it as JSON."""
    return loads(stream.read())
