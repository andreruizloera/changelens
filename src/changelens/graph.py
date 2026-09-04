"""Conservative static import graph of a repository.

Built purely from source text via the language analyzers; nothing is
imported or executed. Edges point from importer to imported module.
`from x import y` is resolved against the set of modules that actually
exist in the repository, so `y` is classified as either a submodule or a
symbol. External imports (stdlib, third-party) are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from changelens.analyzer import ModuleFacts, analyzer_for

_SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    ".eggs",
    "venv",
}


@dataclass
class ImportGraph:
    facts: dict[str, ModuleFacts] = field(default_factory=dict)  # module -> facts
    path_to_module: dict[str, str] = field(default_factory=dict)
    # importer module -> set of internal modules it imports (module edges)
    imports: dict[str, set[str]] = field(default_factory=dict)
    # importer module -> set of (module, symbol) it imports by name
    symbol_imports: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    # importer module -> set of (module, symbol) call targets (approximate)
    calls: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    reverse: dict[str, set[str]] = field(default_factory=dict)  # module -> importers
    unparseable: dict[str, str] = field(default_factory=dict)  # path -> error

    def module_for_path(self, path: str) -> str | None:
        return self.path_to_module.get(path)

    def path_for_module(self, module: str) -> str | None:
        facts = self.facts.get(module)
        return facts.path if facts else None

    def dependents_of(self, module: str) -> set[str]:
        return set(self.reverse.get(module, set()))

    def star_importers_of(self, module: str) -> set[str]:
        return {m for m, f in self.facts.items() if module in f.star_imports}

    def dynamic_importers(self) -> set[str]:
        return {m for m, f in self.facts.items() if f.uses_dynamic_import}

    def raw_dependents_of(self, module: str) -> set[str]:
        """Importers found by raw name, even if `module` no longer exists.

        Used for deleted modules, which have no node in the graph but may
        still be referenced by survivors.
        """
        out: set[str] = set()
        for importer, facts in self.facts.items():
            raw = set(facts.imports) | facts.star_imports
            raw |= {base for base, _ in facts.from_imports}
            raw |= {f"{base}.{name}" for base, name in facts.from_imports}
            if any(r == module or r.startswith(module + ".") for r in raw):
                out.add(importer)
        return out


def _iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(p in _SKIP_DIRS or p.startswith(".") for p in rel_parts[:-1]):
            continue
        if rel_parts[-1].startswith("."):
            continue
        files.append(path)
    return files


def build_graph(root: Path, overrides: dict[str, str | None] | None = None) -> ImportGraph:
    """Scan the working tree and build the import graph.

    `overrides` maps repo-relative paths to alternate file content (for
    analyzing the HEAD or index version of changed files), or to None to
    treat the file as absent.
    """
    overrides = overrides or {}
    graph = ImportGraph()
    sources: dict[str, tuple[str, str, bool]] = {}  # module -> (relpath, source, is_package)

    for path in _iter_source_files(root):
        relpath = path.relative_to(root).as_posix()
        analyzer = analyzer_for(relpath)
        if analyzer is None:
            continue
        module = analyzer.module_name(relpath)
        if module is None:
            continue
        if relpath in overrides:
            content = overrides[relpath]
            if content is None:
                continue
        else:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        is_package = relpath.endswith("__init__.py")
        sources[module] = (relpath, content, is_package)
        graph.path_to_module[relpath] = module

    known = set(sources)
    for module, (relpath, content, is_package) in sources.items():
        analyzer = analyzer_for(relpath)
        assert analyzer is not None
        facts = analyzer.parse_file(content, relpath, module, is_package)
        graph.facts[module] = facts
        if facts.parse_error:
            graph.unparseable[relpath] = facts.parse_error
        _resolve_edges(graph, facts, known)

    for importer, targets in graph.imports.items():
        for target in targets:
            graph.reverse.setdefault(target, set()).add(importer)
    return graph


def _longest_known_prefix(name: str, known: set[str]) -> str | None:
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in known:
            return candidate
    return None


def _resolve_edges(graph: ImportGraph, facts: ModuleFacts, known: set[str]) -> None:
    importer = facts.module
    edges = graph.imports.setdefault(importer, set())
    sym_edges = graph.symbol_imports.setdefault(importer, set())
    call_edges = graph.calls.setdefault(importer, set())

    for name in facts.imports:
        target = _longest_known_prefix(name, known)
        if target and target != importer:
            edges.add(target)

    for base, name in facts.from_imports:
        full = f"{base}.{name}"
        if full in known:  # `from a import b` where a.b is a module
            if full != importer:
                edges.add(full)
        else:
            target = _longest_known_prefix(base, known)
            if target and target != importer:
                edges.add(target)
                if target == base:
                    sym_edges.add((base, name))

    for star_target in facts.star_imports:
        target = _longest_known_prefix(star_target, known)
        if target and target != importer:
            edges.add(target)

    for chain in facts.calls:
        target = _longest_known_prefix(chain, known)
        if target and target != chain and target != importer:
            # First segment past the module is the top-level symbol, which
            # covers both `mod.func()` and `mod.Class.method()` chains.
            rest = chain[len(target) + 1 :]
            call_edges.add((target, rest.split(".")[0]))
