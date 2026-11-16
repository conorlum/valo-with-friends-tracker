"""Lossless compact JSON values, omitting only null dictionary fields.

Objects and scalar lists use braces and brackets. A dictionary list is a table:
<key|key; followed by rows separated by semicolons/newlines>. Blank cells mean
absent fields. Areas use ~(name|super|path|approximate), with the same blank rule.
Ambiguous strings are JSON quoted; numbers retain their JSON representation.
Query-only short mode uses (name[ancestor names]^super)~ for areas, reconstructing
the final name in path, and @(from direction min-maxm) for rough places. A trailing
~ means approximate true, and no mark means false. Areas with absent or non-bool
flags and ambiguous shapes retain the full form. Short table rows omit trailing
blank cells.
"""

import json
import math
import re

_AREA = ("name", "super", "path", "approximate")
_DELIMITERS = frozenset('{}[]()<>:,|;\n\r')
_ROUGH = ("from", "direction", "metres_min", "metres_max")
_NUMBER = r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
_ROUGH_PATTERN = re.compile(r"(.+) (up|down|left|right) (" + _NUMBER + r")-(" + _NUMBER + r")m")


def _string(value: str) -> str:
    if (not value or value != value.strip() or any(c in _DELIMITERS or c in '\\"~' for c in value)
            or any(ord(c) < 32 for c in value)):
        return json.dumps(value, ensure_ascii=False)
    try:
        json.loads(value)
    except ValueError:
        return value
    return json.dumps(value, ensure_ascii=False)


def _short_label(value) -> bool:
    return (isinstance(value, str) and _string(value) == value
            and not any(mark in value for mark in "^@~"))


def _short_area(fields) -> str | None:
    name, path = fields.get("name"), fields.get("path")
    if (not set(fields) <= set(_AREA) or not _short_label(name)
            or not isinstance(path, list) or not path or path[-1] != name or path.count(name) != 1
            or not all(_short_label(part) for part in path)
            or ("super" in fields and not _short_label(fields["super"]))
            or type(fields.get("approximate")) is not bool):
        return None
    ancestors = render(path[:-1], short=True) if len(path) > 1 else ""
    super_name = "^" + fields["super"] if "super" in fields else ""
    return "(" + name + ancestors + super_name + ")" + ("~" if fields.get("approximate") else "")


def _short_rough(fields) -> str | None:
    if (set(fields) != set(_ROUGH) or not _short_label(fields["from"])
            or fields["direction"] not in ("up", "down", "left", "right")
            or any(type(fields[k]) not in (int, float) for k in _ROUGH[2:])
            or any(isinstance(fields[k], float) and not math.isfinite(fields[k]) for k in _ROUGH[2:])):
        return None
    return ("@(" + fields["from"] + " " + fields["direction"] + " "
            + json.dumps(fields["metres_min"]) + "-" + json.dumps(fields["metres_max"]) + "m)")


def render(value, short=False) -> str:
    """Render without mutating the input; null list items and empty containers stay."""
    if isinstance(value, dict):
        fields = {k: v for k, v in value.items() if v is not None}
        if short:
            abbreviated = _short_area(fields) or _short_rough(fields)
            if abbreviated is not None:
                return abbreviated
        if "name" in fields and "path" in fields and set(fields) <= set(_AREA):
            return "~(" + "|".join(render(fields[k], short=short) if k in fields else "" for k in _AREA) + ")"
        return "{" + ",".join(_string(k) + ":" + render(v, short=short) for k, v in fields.items()) + "}"
    if isinstance(value, list):
        if value and all(isinstance(row, dict) for row in value):
            keys = list(dict.fromkeys(k for row in value for k, v in row.items() if v is not None))
            rows = ["|".join(_string(k) for k in keys)]
            for row in value:
                cells = "|".join(render(row[k], short=short) if row.get(k) is not None else "" for k in keys)
                rows.append(cells.rstrip("|") if short else cells)
            return "<" + ";\n".join(rows) + ">"
        return "[" + ",".join(render(v, short=short) for v in value) + "]"
    if isinstance(value, str):
        return _string(value)
    return json.dumps(value)


class _Parser:
    def __init__(self, text, short=False):
        self.text, self.i = text, 0
        self.short = short

    def take(self, token):
        if not self.text.startswith(token, self.i):
            raise ValueError(f"expected {token!r} at {self.i}")
        self.i += len(token)

    def peek(self):
        return self.text[self.i:self.i + 1]

    def fields(self, end):
        """Read a pipe-separated row, preserving blank cells as absent fields."""
        row = []
        while True:
            row.append(self.value() if self.peek() not in ("|", end, ">") else _ABSENT)
            if self.peek() != "|":
                return row
            self.take("|")

    def label(self):
        begin = self.i
        while self.peek() and self.peek() not in _DELIMITERS and self.peek() != "^":
            self.i += 1
        label = self.text[begin:self.i]
        if not _short_label(label):
            raise ValueError(f"invalid area label at {begin}")
        return label

    def value(self):
        start = self.peek()
        if self.short and start == "(":
            self.take("(")
            name = self.label()
            ancestors = self.value() if self.peek() == "[" else []
            if not isinstance(ancestors, list) or not all(_short_label(part) for part in ancestors):
                raise ValueError("area ancestors must be names")
            result = {"name": name, "path": ancestors + [name]}
            if self.peek() == "^":
                self.take("^")
                result["super"] = self.label()
            self.take(")")
            result["approximate"] = self.peek() == "~"
            if self.peek() == "~":
                self.take("~")
            return result
        if self.short and self.text.startswith("@(", self.i):
            self.take("@(")
            end = self.text.find(")", self.i)
            match = _ROUGH_PATTERN.fullmatch(self.text[self.i:end]) if end >= 0 else None
            if match is None:
                raise ValueError(f"invalid rough place at {self.i}")
            self.i = end + 1
            source, direction, low, high = match.groups()
            return dict(zip(_ROUGH, (source, direction, json.loads(low), json.loads(high))))
        if start == '"':
            value, self.i = json.JSONDecoder().raw_decode(self.text, self.i)
            return value
        if start == "{":
            self.take("{")
            result = {}
            while self.peek() != "}":
                key = self.value()
                if not isinstance(key, str):
                    raise ValueError("object key must be a string")
                self.take(":")
                result[key] = self.value()
                if self.peek() != ",":
                    break
                self.take(",")
            self.take("}")
            return result
        if start == "[":
            self.take("[")
            result = []
            while self.peek() != "]":
                result.append(self.value())
                if self.peek() != ",":
                    break
                self.take(",")
            self.take("]")
            return result
        if start == "~":
            self.take("~(")
            fields = self.fields(")")
            self.take(")")
            if len(fields) != len(_AREA):
                raise ValueError("area must have four cells")
            return {k: v for k, v in zip(_AREA, fields) if v is not _ABSENT}
        if start == "<":
            self.take("<")
            keys = [] if self.peek() == ";" else self.fields(";")
            if any(not isinstance(k, str) for k in keys):
                raise ValueError("table keys must be strings")
            result = []
            while self.peek() == ";":
                self.take(";")
                if self.peek() == "\n":
                    self.take("\n")
                # The last cell may end at either the next row or the table end.
                fields = self.fields(";") if keys else []
                if len(fields) > len(keys) or (not self.short and len(fields) != len(keys)):
                    raise ValueError("table row has wrong cell count")
                result.append({k: v for k, v in zip(keys, fields) if v is not _ABSENT})
            self.take(">")
            return result
        begin = self.i
        while self.peek() and self.peek() not in _DELIMITERS:
            self.i += 1
        if begin == self.i:
            raise ValueError(f"expected value at {self.i}")
        token = self.text[begin:self.i]
        try:
            return json.loads(token)
        except ValueError:
            return token


_ABSENT = object()


def parse(text: str, short=False):
    """Read a rendered block back into JSON-compatible values."""
    parser = _Parser(text, short=short)
    value = parser.value()
    if parser.i != len(text):
        raise ValueError(f"unexpected text at {parser.i}")
    return value
