from __future__ import annotations

from changelens.python_analyzer import PythonAnalyzer

ANALYZER = PythonAnalyzer()

SOURCE = '''\
"""Module docstring."""

CONSTANT = 1


def top(x):
    return x + 1


class Service:
    limit = 5

    def process(self, item):
        if item:
            return item
        return None

    async def flush(self):
        return self.limit


@property
def decorated(self):
    return 1
'''


def test_line_in_function() -> None:
    spans = ANALYZER.map_lines_to_symbols(SOURCE, {7})
    assert spans is not None
    assert [(s.qualname, s.kind) for s in spans] == [("top", "function")]


def test_line_in_method_gets_qualified_name() -> None:
    spans = ANALYZER.map_lines_to_symbols(SOURCE, {14})
    assert spans is not None
    assert [s.qualname for s in spans] == ["Service.process"]


def test_line_in_async_method() -> None:
    spans = ANALYZER.map_lines_to_symbols(SOURCE, {19})
    assert spans is not None
    assert [s.qualname for s in spans] == ["Service.flush"]


def test_class_body_line_maps_to_class() -> None:
    spans = ANALYZER.map_lines_to_symbols(SOURCE, {11})
    assert spans is not None
    assert [(s.qualname, s.kind) for s in spans] == [("Service", "class")]


def test_module_level_line_yields_no_spans() -> None:
    spans = ANALYZER.map_lines_to_symbols(SOURCE, {3})
    assert spans == []


def test_decorator_line_maps_to_decorated_function() -> None:
    spans = ANALYZER.map_lines_to_symbols(SOURCE, {22})
    assert spans is not None
    assert [s.qualname for s in spans] == ["decorated"]


def test_multiple_lines_dedupe_symbols() -> None:
    spans = ANALYZER.map_lines_to_symbols(SOURCE, {13, 14, 15})
    assert spans is not None
    assert [s.qualname for s in spans] == ["Service.process"]


def test_unparseable_source_returns_none() -> None:
    assert ANALYZER.map_lines_to_symbols("def broken(:\n", {1}) is None
