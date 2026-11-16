"""Tests for jsonparse.

Covers the MANIFEST requirements: roundtrip on spec fixtures, every
malformed class rejected with correct line/column, surrogate pairs to a
single codepoint, floats matching :mod:`json`, canonical-form stability,
defined big-integer precision, the three serializer modes, cycle detection,
and JSON Lines streaming — plus the caret error presentation.
"""

import io
import json
import math
import os
import tempfile
import unittest

from jsonparse import (
    JSONError,
    canonical,
    dump,
    dumps,
    iter_json_lines,
    load,
    loads,
    loads_lines,
)

SPEC_FIXTURES = [
    '{"a": 1, "b": [true, false, null]}',
    '{"nested": {"deep": {"x": [1, [2, [3]]]}}}',
    '"escapes: \\" \\\\ / \\b \\f \\n \\r \\t"',
    '"\\u0041\\u00e9\\u20ac"',
    "[-0, 0, -0.0, 1e3, 1E+3, 1.5e-3, -2.5E10]",
    '{"empty_obj": {}, "empty_arr": []}',
    '"\\uD83D\\uDE00"',
    '  {"ws":\t[1,\r\n2]}  ',
    '{"unicode": "caf\\u00e9 \\u2713"}',
    "[[[[[1]]]]]",
]

MALFORMED = [
    # (label, text, line, column, expected fragment)
    ("unclosed string", '"abc', 1, 5, "expected '\"'"),
    ("trailing comma array", "[1,]", 1, 4, "expected value"),
    ("trailing comma object", '{"a":1,}', 1, 8, "expected string key"),
    ("missing colon", '{"a" 1}', 1, 6, "expected ':'"),
    ("unquoted key", "{a:1}", 1, 2, "expected string key"),
    ("unclosed array", "[1", 1, 3, "expected one of ',', ']'"),
    ("unclosed object", '{"a":1', 1, 7, "expected one of ',', '}'"),
    ("leading zero", "01", 1, 2, "expected end of input"),
    ("dangling dot", "1.", 1, 3, "expected digit"),
    ("plus sign", "+1", 1, 1, "expected value"),
    ("dot start", ".5", 1, 1, "expected value"),
    ("lowercase nan", "nan", 1, 2, "expected 'null'"),
    ("bad escape", '"a\\qb"', 1, 4, "expected valid escape"),
    ("short hex", '"\\u12"', 1, 6, "expected hex digit"),
    ("lone high surrogate", '"\\uD800"', 1, 8, "low surrogate"),
    ("lone low surrogate", '"\\uDE00"', 1, 2, "unpaired low surrogate"),
    ("raw control char", '"a\nb"', 1, 3, "unescaped control character"),
    ("empty input", "", 1, 1, "expected value"),
    ("trailing content", "{} {}", 1, 4, "expected end of input"),
    ("NBSP is not whitespace", "\u00a042", 1, 1, "expected value"),
    ("truncated true", "tru", 1, 4, "expected 'true'"),
    ("missing comma", "[1 2]", 1, 4, "expected one of ',', ']'"),
    ("garbage", "x", 1, 1, "expected value"),
    ("NaN in context", '{\n  "v": NaN\n}', 2, 8, "not valid JSON numbers"),
    ("Infinity in context", '{\n  "v": Infinity\n}', 2, 8,
     "not valid JSON numbers"),
    ("negative infinity", "-Infinity", 1, 1, "not valid JSON numbers"),
    ("trailing comma multiline", "[1,\n2,\n]", 3, 1, "expected value"),
    ("object comma multiline", '{\n  "a": 1,\n}', 3, 1, "expected string key"),
]


class TestParsingFixtures(unittest.TestCase):
    def test_roundtrip_and_agreement_with_json_module(self):
        for text in SPEC_FIXTURES:
            with self.subTest(text=text):
                ours = loads(text)
                self.assertEqual(ours, json.loads(text))
                self.assertEqual(loads(dumps(ours)), ours)
                self.assertEqual(loads(dumps(ours, ensure_ascii=True)), ours)

    def test_rfc_whitespace_is_exactly_four_characters(self):
        self.assertEqual(loads('\t\n\r  42  '), 42)
        with self.assertRaises(JSONError):
            loads("\x0b42")  # vertical tab is not JSON whitespace
        with self.assertRaises(JSONError):
            loads("\x0c42")  # form feed is not JSON whitespace

    def test_utf8_bom_is_tolerated(self):
        self.assertEqual(loads('\ufeff{"k": 1}'), {"k": 1})

    def test_duplicate_keys_last_one_wins(self):
        # documented policy: last occurrence wins (same as the json module)
        self.assertEqual(loads('{"a": 1, "a": 2, "a": 3}'), {"a": 3})
        self.assertEqual(json.loads('{"a": 1, "a": 2, "a": 3}'), {"a": 3})

    def test_empty_containers_and_nesting(self):
        self.assertEqual(loads("{}"), {})
        self.assertEqual(loads("[]"), [])
        self.assertEqual(loads('{"a": {}}'), {"a": {}})
        self.assertEqual(loads("[[]]"), [[]])
        self.assertEqual(loads('{"a": []}'), {"a": []})


class TestNumbers(unittest.TestCase):
    def test_floats_match_json_module(self):
        corpus = [
            "0.1", "-3.14e-2", "1e21", "5e-324",
            "1.7976931348623157e308", "12345.6789", "1e-2", "2.5e10",
            "0.0", "-0.0", "3.0e+2",
        ]
        for text in corpus:
            with self.subTest(text=text):
                ours = loads(text)
                self.assertEqual(ours, json.loads(text))
                self.assertIs(type(ours), float)

    def test_int_float_and_bool_type_boundaries(self):
        self.assertIs(type(loads("1")), int)
        self.assertIs(type(loads("1.0")), float)
        self.assertIs(type(loads("true")), bool)
        self.assertIs(loads("true"), True)
        self.assertIs(type(loads("-0")), int)
        self.assertEqual(loads("-0"), 0)

    def test_exponents_and_negative_zero(self):
        self.assertEqual(loads("1e3"), 1000.0)
        self.assertEqual(loads("1E+2"), 100.0)
        self.assertEqual(loads("-0e0"), -0.0)
        self.assertEqual(math.copysign(1, loads("-0.0")), -1)
        self.assertEqual(math.copysign(1, loads("-0e0")), -1)
        self.assertEqual(math.copysign(1, loads("-0")), 1)  # int 0, no sign

    def test_big_integers_are_exact_defined_precision(self):
        # policy: ints are arbitrary precision (exact); floats are doubles
        big = 2**70 + 1
        self.assertEqual(loads(str(big)), big)
        fifty = int("9" * 50)
        self.assertEqual(loads(str(fifty)), fifty)
        self.assertEqual(loads(str(-fifty)), -fifty)

    def test_out_of_range_exponent_matches_json_module(self):
        # 1e400 overflows the double: both parsers yield inf (documented)
        self.assertEqual(loads("1e400"), json.loads("1e400"))
        self.assertTrue(math.isinf(loads("1e400")))
        self.assertEqual(loads("-1e400"), json.loads("-1e400"))


class TestMalformedInputs(unittest.TestCase):
    def test_every_malformed_class_reports_exact_line_and_column(self):
        for label, text, line, column, fragment in MALFORMED:
            with self.subTest(case=label):
                with self.assertRaises(JSONError) as ctx:
                    loads(text)
                exc = ctx.exception
                self.assertEqual(exc.line, line, label)
                self.assertEqual(exc.column, column, label)
                self.assertIn(fragment, str(exc))
                self.assertTrue(str(exc).startswith(f"line {line}, column {column}"))

    def test_error_context_renders_caret_under_the_column(self):
        with self.assertRaises(JSONError) as ctx:
            loads("[1,\n  2,\n  ]")
        exc = ctx.exception
        self.assertEqual((exc.line, exc.column), (3, 3))
        self.assertEqual(exc.context(), "  ]\n  ^")
        self.assertIn("\n  ]\n  ^", str(exc))
        self.assertEqual(exc.found, "]")

    def test_error_found_is_end_of_input_at_eof(self):
        with self.assertRaises(JSONError) as ctx:
            loads('"abc')
        self.assertEqual(ctx.exception.found, "end of input")
        self.assertEqual(ctx.exception.expected, frozenset({repr('"')}))

    def test_json_error_is_a_value_error(self):
        with self.assertRaises(ValueError):
            loads("nope")


class TestSurrogates(unittest.TestCase):
    def test_emoji_surrogate_pair_is_a_single_codepoint(self):
        decoded = loads('"\\uD83D\\uDE00"')
        self.assertEqual(decoded, "\U0001F600")
        self.assertEqual(len(decoded), 1)
        self.assertEqual(ord(decoded), 0x1F600)

    def test_pair_chain_and_bmp_escapes(self):
        self.assertEqual(loads('"\\u0041\\u00e9"'), "Aé")
        self.assertEqual(loads('"\\uD83D\\uDE00\\uD83D\\uDE00"'), "\U0001F600" * 2)
        self.assertEqual(loads('"\\u0000"'), "\x00")  # escaped NUL is legal

    def test_second_escape_must_be_a_low_surrogate(self):
        with self.assertRaises(JSONError) as ctx:
            loads('"\\uD800\\uD800"')
        exc = ctx.exception
        self.assertEqual((exc.line, exc.column), (1, 8))
        self.assertIn("low surrogate", str(exc))

    def test_ensure_ascii_roundtrips_astral_characters(self):
        text = "café \U0001F600"
        encoded = dumps(text, ensure_ascii=True)
        self.assertEqual(encoded, '"caf\\u00e9 \\ud83d\\ude00"')
        self.assertEqual(loads(encoded), text)


class TestSerializer(unittest.TestCase):
    def test_indent_golden_output(self):
        obj = {"b": [1, 2], "a": {"z": True, "y": None}}
        self.assertEqual(
            dumps(obj, indent=2),
            '{\n  "b": [\n    1,\n    2\n  ],\n'
            '  "a": {\n    "z": true,\n    "y": null\n  }\n}',
        )
        self.assertEqual(dumps({}, indent=2), "{}")
        self.assertEqual(dumps([], indent=2), "[]")
        self.assertEqual(dumps(42, indent=2), "42")

    def test_compact_and_default_modes(self):
        obj = {"b": [1, 2], "a": {"z": True, "y": None}}
        self.assertEqual(
            dumps(obj, compact=True), '{"b":[1,2],"a":{"z":true,"y":null}}'
        )
        self.assertEqual(dumps({"a": 1, "b": [1, 2]}), '{"a": 1, "b": [1, 2]}')
        self.assertEqual(dumps([1, 2]), "[1, 2]")

    def test_sort_keys_at_every_level(self):
        obj = {"b": [1, {"d": 1, "c": 2}], "a": 3}
        self.assertEqual(
            dumps(obj, sort_keys=True), '{"a": 3, "b": [1, {"c": 2, "d": 1}]}'
        )

    def test_stream_dump_and_load(self):
        buffer = io.StringIO()
        dump({"k": "v"}, buffer)
        self.assertEqual(buffer.getvalue(), '{"k": "v"}')
        self.assertEqual(load(io.StringIO('{"k": "v"}')), {"k": "v"})

    def test_non_finite_floats_raise_value_error(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    dumps(bad)

    def test_unsupported_types_raise_type_error(self):
        with self.assertRaises(TypeError):
            dumps(object())
        with self.assertRaises(TypeError):
            dumps({1: "int key"})
        with self.assertRaises(TypeError):
            dumps("text", indent="2")

    def test_cycle_detection_raises_value_error(self):
        cyclic_dict = {}
        cyclic_dict["self"] = cyclic_dict
        with self.assertRaises(ValueError):
            dumps(cyclic_dict)

        cyclic_list = []
        cyclic_list.append(cyclic_list)
        with self.assertRaises(ValueError):
            canonical(cyclic_list)

        mutual_a, mutual_b = {}, {}
        mutual_a["b"] = mutual_b
        mutual_b["a"] = mutual_a
        with self.assertRaises(ValueError):
            dumps(mutual_a)

    def test_shared_non_cyclic_references_are_allowed(self):
        shared = [1, 2]
        self.assertEqual(dumps([shared, shared]), "[[1, 2], [1, 2]]")


class TestCanonicalForm(unittest.TestCase):
    def test_canonical_golden(self):
        obj = {"b": 1.5, "a": [2, {"d": 1, "c": 2}]}
        self.assertEqual(canonical(obj), '{"a":[2,{"c":2,"d":1}],"b":1.5}')

    def test_canonical_is_stable_and_roundtrips(self):
        for text in SPEC_FIXTURES:
            with self.subTest(text=text):
                obj = loads(text)
                once = canonical(obj)
                self.assertEqual(canonical(loads(once)), once)
                self.assertEqual(loads(once), obj)

    def test_minimal_float_repr(self):
        self.assertEqual(canonical(0.1), "0.1")
        self.assertEqual(canonical(1.0), "1.0")
        self.assertEqual(canonical(1e21), "1e+21")
        self.assertEqual(canonical(-0.0), "-0.0")
        self.assertEqual(canonical(5e-324), "5e-324")
        self.assertEqual(loads(canonical(0.1)), 0.1)


class TestJsonLines(unittest.TestCase):
    TEXT = '{"n": 1}\n\n[2, 3]\n"four"\n'

    def test_loads_lines_parses_each_record(self):
        self.assertEqual(
            loads_lines(self.TEXT), [{"n": 1}, [2, 3], "four"]
        )

    def test_bare_string_source_is_split_into_lines(self):
        self.assertEqual(
            list(iter_json_lines(self.TEXT)), [{"n": 1}, [2, 3], "four"]
        )

    def test_streaming_from_file_object_is_lazy(self):
        path = os.path.join(tempfile.gettempdir(), "jsonparse_test.jsonl")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write('{"a": 1}\n{"b": 2}\n{"c": 3}\n')
        try:
            with open(path, "r", encoding="utf-8") as handle:
                stream = iter_json_lines(handle)
                self.assertEqual(next(stream), {"a": 1})
                self.assertEqual(next(stream), {"b": 2})
                self.assertEqual(list(stream), [{"c": 3}])
        finally:
            os.remove(path)

    def test_bad_record_reports_its_record_number(self):
        with self.assertRaises(JSONError) as ctx:
            loads_lines("1\n2\n[oops]\n")
        exc = ctx.exception
        self.assertEqual(exc.record, 3)
        self.assertIn("[JSON Lines record 3]", str(exc))
        self.assertEqual((exc.line, exc.column), (1, 2))
        self.assertEqual(exc.found, "o")

    def test_blank_and_whitespace_lines_are_skipped(self):
        self.assertEqual(loads_lines("\n  \n1\n\t\n2\n"), [1, 2])

    def test_no_trailing_newline_required(self):
        self.assertEqual(loads_lines('{"a": 1}'), [{"a": 1}])


if __name__ == "__main__":
    unittest.main()
