"""jsonparse — JSON, from first principles.

Strict RFC 8259 parser with caret-annotated errors, a serializer with
indent/sort_keys/compact modes, canonical form, and a JSON Lines
streaming reader. Zero dependencies.

Public API::

    from jsonparse import loads, load, dumps, dump, canonical
    from jsonparse import iter_json_lines, loads_lines, JSONError

    loads('{"a": [1, 2.5, -0]}')      # -> {'a': [1, 2.5, 0]}
    dumps(obj, indent=2)               # pretty
    canonical(obj)                     # sorted keys, compact, minimal floats
    list(iter_json_lines(open(...)))   # streaming JSON Lines
"""

from .jsonlines import iter_json_lines, loads_lines
from .parser import JSONError, load, loads
from .writer import canonical, dump, dumps

__all__ = [
    "JSONError",
    "canonical",
    "dump",
    "dumps",
    "iter_json_lines",
    "load",
    "loads",
    "loads_lines",
]

__version__ = "1.0.0"
