# jsonparse

JSON, from first principles: a strict **RFC 8259** parser written as a
hand-scanned recursive descent (no regexes), so every rejection can point at
the exact offending line and column — with a caret — plus a serializer with
`indent` / `sort_keys` / `compact` modes, a **canonical form**, a streaming
**JSON Lines** reader, and cycle detection on output.

Zero dependencies — standard library only.

## Why it's special

- **Precise, caret-annotated errors.** Every malformed input class reports
  `line`, `column`, the expected tokens, what was found, and a caret line:

  ```
  >>> loads('[1, 2,]')
  Traceback (most recent call last):
      ...
  jsonparse.parser.JSONError: line 1, column 7: expected value; found ']'
  [1, 2,]
        ^
  ```

- **Full string grammar.** All escapes, `\uXXXX` with correct **surrogate
  pairing** (`"\uD83D\uDE00"` → one codepoint `😀`), rejection of lone
  surrogates (pointing at the offending backslash), rejection of raw control
  characters, and an explicit message for `NaN`/`Infinity`.
- **Numbers with defined precision.** Integers are arbitrary precision and
  exact; fraction/exponent forms become C doubles (byte-for-byte agreement
  with the `json` module); `-0` is integer `0` while `-0.0`/`-0e0` keep the
  sign; `1e400` overflows to `inf` exactly like `json` (documented).
- **Three serializer modes + canonical form.** Python-style `indent`,
  `sort_keys`, `compact`, `ensure_ascii` (with surrogate pairs on output);
  `canonical()` = sorted keys + compact separators + minimal float repr,
  stable under round-tripping: `canonical(loads(canonical(x))) == canonical(x)`.
- **JSON Lines streaming.** `iter_json_lines()` lazily yields one record per
  line (file objects, lists, or a raw `str`), skipping blank lines; a bad
  record raises with `exc.record` telling you which line of the stream broke.
- **Safe output.** `NaN`/`Infinity` raise `ValueError` instead of emitting
  non-JSON tokens, container **cycles** raise `ValueError`, non-`str` keys
  raise `TypeError`, and shared-but-acyclic references serialize fine.

## Runnable example

```python
>>> from jsonparse import loads, dumps, canonical, JSONError
>>> loads('{"name": "caf\u00e9", "tags": ["a", "b"], "n": -0}')
{'name': 'café', 'tags': ['a', 'b'], 'n': 0}
>>> loads('"\\uD83D\\uDE00"')
'😀'
>>> loads("1e3"), loads("123456789012345678901234567890")
(1000.0, 123456789012345678901234567890)
>>> dumps({"b": [1, 2], "a": True}, indent=2)
'{\n  "b": [\n    1,\n    2\n  ],\n  "a": true\n}'
>>> dumps({"b": 1.5, "a": [2, {"d": 1, "c": 2}]}, sort_keys=True, compact=True)
'{"a":[2,{"c":2,"d":1}],"b":1.5}'
>>> canonical({"b": 1.5, "a": [2, {"d": 1, "c": 2}]})
'{"a":[2,{"c":2,"d":1}],"b":1.5}'
```

### Errors you can act on

```python
>>> loads('"\\uDE00"')
Traceback (most recent call last):
    ...
jsonparse.parser.JSONError: line 1, column 2: unpaired low surrogate \uDE00 in string
"\uDE00"
 ^
>>> loads('{"a": 1,}')       # trailing comma
Traceback (most recent call last):
    ...
jsonparse.parser.JSONError: line 1, column 8: expected string key; found '}'
{"a": 1,}
        ^
>>> node = {}; node["self"] = node
>>> dumps(node)
Traceback (most recent call last):
    ...
ValueError: Circular reference detected in dict at serialization time
```

### JSON Lines

```python
>>> from jsonparse import iter_json_lines, loads_lines
>>> list(iter_json_lines('{"n": 1}\n[2, 3]\n'))
[{'n': 1}, [2, 3]]
>>> loads_lines("1\n[oops]\n")
Traceback (most recent call last):
    ...
jsonparse.parser.JSONError: line 1, column 2: expected value; found 'o' [JSON Lines record 2]
[oops]
 ^
```

## Documented policies

| Policy | Behavior |
| --- | --- |
| Duplicate object keys | **Last occurrence wins** (identical to the `json` module) |
| Big integers | Arbitrary precision — exact, no lossy conversion |
| Floats | C doubles; agreement with `json` on the whole test corpus; output via `repr` (shortest round-trip form) |
| Out-of-range exponents | `1e400` parses to `inf` (like `json`); serialization refuses non-finite floats |
| Whitespace | Exactly the four RFC characters: space, tab, LF, CR — `NBSP`, vertical tab and form feed are errors |
| BOM | A leading UTF-8 BOM is tolerated (Windows interop), never emitted |
| `NaN` / `Infinity` | Rejected on input **and** on output (strict RFC both ways) |
| Lone surrogates | Rejected on input; output escaping (`ensure_ascii`) reproduces pairs |

## Architecture

| File | Contents |
| --- | --- |
| `jsonparse/parser.py` | hand-written scanner: value/number/string/array/object readers, surrogate pairing, `JSONError` (position, expected set, caret `context()`) |
| `jsonparse/writer.py` | `dumps`/`dump`/`canonical`: indent layout, compact & sorted modes, `ensure_ascii` escaping, cycle detection, finite-float enforcement |
| `jsonparse/jsonlines.py` | lazy JSON Lines iterator + `loads_lines` convenience, record-numbered errors |
| `jsonparse/__init__.py` | public API re-exports |

## Tests

From the project root:

```
python -m unittest discover -s tests -v
```

35 tests: roundtrip + `json`-module agreement on ten spec fixtures, 28
malformed inputs each asserted at the exact line/column with message
fragment, surrogate pairs (including chain and second-must-be-low cases),
float corpus vs `json`, big-int exactness, `-0` sign semantics, golden
outputs for indent/compact/sorted/canonical modes, canonical stability,
cycle detection (self, list, mutual) vs shared-reference allowance,
non-finite/type errors, caret rendering, and JSON Lines (file streaming,
record numbers, blank lines, bare-string source).

## Files

- `jsonparse/parser.py` — strict RFC 8259 parser with precise errors
- `jsonparse/writer.py` — serializer modes + canonical form + cycles
- `jsonparse/jsonlines.py` — streaming JSON Lines reader
- `tests/test_jsonparse.py` — the suite
