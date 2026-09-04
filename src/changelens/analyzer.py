"""Language analyzer interface.

changelens is built around a small per-language analyzer contract so that
support for other languages (TypeScript, Go, Rust, Java) can be added
without touching the git, graph, impact, or reporting layers. Each
analyzer knows how to:

1. claim source files by extension,
2. derive a module name from a repository-relative path,
3. extract facts from one file (imports, star imports, call targets,
   dynamic-import usage) as absolute dotted module names,
4. map changed line numbers back to the enclosing symbol definitions,
5. recognize test files for its ecosystem.

Only the Python analyzer exists in v0.1 (see python_analyzer.py). The
generic layers consume analyzers exclusively through this interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ModuleFacts:
    """Everything the graph layer needs to know about one source file.

    All module references are absolute dotted names; relative imports are
    resolved by the analyzer before the facts leave the file.
    """

    module: str
    path: str  # repository-relative posix path
    is_package: bool = False
    imports: set[str] = field(default_factory=set)  # `import x.y` targets
    from_imports: set[tuple[str, str]] = field(default_factory=set)  # (module, name)
    star_imports: set[str] = field(default_factory=set)  # `from x import *` targets
    calls: set[str] = field(default_factory=set)  # absolute dotted call targets
    uses_dynamic_import: bool = False
    unresolved_relative_import: bool = False
    parse_error: str | None = None


@dataclass(frozen=True)
class SymbolSpan:
    """A symbol definition that encloses one or more changed lines."""

    qualname: str  # e.g. "refund_payment" or "RefundService.process"
    kind: str  # "function" | "class"
    start: int
    end: int


@runtime_checkable
class LanguageAnalyzer(Protocol):
    """Contract every per-language analyzer implements."""

    language: str
    extensions: tuple[str, ...]

    def module_name(self, relpath: str) -> str | None:
        """Dotted module name for a repo-relative path, or None to skip."""
        ...

    def parse_file(self, source: str, relpath: str, module: str, is_package: bool) -> ModuleFacts:
        """Extract import/call facts. Must not raise on bad syntax; set parse_error."""
        ...

    def map_lines_to_symbols(self, source: str, lines: set[int]) -> list[SymbolSpan] | None:
        """Innermost defs enclosing the given lines; None if unparseable."""
        ...

    def is_test_file(self, relpath: str) -> bool:
        """Whether this path is a test file in the language's ecosystem."""
        ...


_REGISTRY: list[LanguageAnalyzer] = []


def register(analyzer: LanguageAnalyzer) -> None:
    _REGISTRY.append(analyzer)


def analyzers() -> list[LanguageAnalyzer]:
    if not _REGISTRY:
        from changelens.python_analyzer import PythonAnalyzer

        register(PythonAnalyzer())
    return list(_REGISTRY)


def analyzer_for(relpath: str) -> LanguageAnalyzer | None:
    return next((a for a in analyzers() if relpath.endswith(a.extensions)), None)
