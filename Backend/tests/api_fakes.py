"""Helpers for shaping fake async DB results in API route tests."""

from __future__ import annotations


class ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class ScalarsResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class RowsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return self._rows


def scalar_one_result(value):
    return ScalarResult(value)


def scalar_all_result(items):
    return ScalarsResult(items)


def rows_result(rows):
    return RowsResult(rows)
