"""Lossless compact JSON values, omitting only null dictionary fields.

Objects and scalar lists use braces and brackets. A dictionary list is a table:
<key|key; followed by rows separated by semicolons/newlines>. Blank cells mean
absent fields. Areas use ~(name|super|path|approximate), with the same blank rule.
Ambiguous strings are JSON quoted; numbers retain their JSON representation.
"""

import json

_AREA = ("name", "super", "path", "approximate")
_DELIMITERS = frozenset('{}[]()<>:,|;\n\r')


def _string(value: str) -> str:
    if (not value or value != value.strip() or any(c in _DELIMITERS or c in '\\"~' for c in value)
            or any(ord(c) < 32 for c in value)):
        return json.dumps(value, ensure_ascii=False)
    try:
        json.loads(value)
    except ValueError:
        return value
    return json.dumps(value, ensure_ascii=False)


def render(value) -> str:
    """Render without mutating the input; null list items and empty containers stay."""
    if isinstance(value, dict):
        fields = {k: v for k, v in value.items() if v is not None}
        if "name" in fields and "path" in fields and set(fields) <= set(_AREA):
            return "~(" + "|".join(render(fields[k]) if k in fields else "" for k in _AREA) + ")"
        return "{" + ",".join(_string(k) + ":" + render(v) for k, v in fields.items()) + "}"
    if isinstance(value, list):
        if value and all(isinstance(row, dict) for row in value):
            keys = list(dict.fromkeys(k for row in value for k, v in row.items() if v is not None))
            rows = ["|".join(_string(k) for k in keys)]
            rows += ["|".join(render(row[k]) if row.get(k) is not None else "" for k in keys) for row in value]
            return "<" + ";\n".join(rows) + ">"
        return "[" + ",".join(render(v) for v in value) + "]"
    if isinstance(value, str):
        return _string(value)
    return json.dumps(value)


class _Parser:
    def __init__(self, text):
        self.text, self.i = text, 0

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

    def value(self):
        start = self.peek()
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
                if len(fields) != len(keys):
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


def parse(text: str):
    """Read a rendered block back into JSON-compatible values."""
    parser = _Parser(text)
    value = parser.value()
    if parser.i != len(text):
        raise ValueError(f"unexpected text at {parser.i}")
    return value
