"""Turn a diff plus an import graph into a ranked blast-radius report.

Ranking rules:
- direct dependents outrank transitive dependents,
- within direct dependents, modules that call a changed symbol outrank
  modules that merely import the changed module,
- transitive dependents are ordered by import distance, then path.

Confidence is a labeled heuristic, not a proof:
- High: every changed file parsed, every changed symbol resolved, and no
  construct that could hide an edge was seen near the affected set.
- Medium: star imports or dynamic imports (importlib / __import__) exist
  that could hide dependents, or an unchanged file failed to parse.
- Low: a changed file could not be parsed, or a relative import could
  not be resolved, so the analysis itself is incomplete.
Every downgrade carries an explicit reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from changelens.analyzer import analyzer_for
from changelens.gitdiff import FileDiff, file_content
from changelens.graph import ImportGraph, build_graph

HIGH = "High"
MEDIUM = "Medium"
LOW = "Low"

_LEVEL_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}


@dataclass(frozen=True)
class ChangedSymbol:
    file: str
    qualname: str | None  # None means the change is module-level
    kind: str  # "function" | "class" | "module"

    @property
    def display(self) -> str:
        return f"{self.file}::{self.qualname}" if self.qualname else self.file


@dataclass(frozen=True)
class Dependent:
    file: str
    module: str
    distance: int
    calls_changed_symbol: bool = False
    called_symbols: tuple[str, ...] = ()
    via: str | None = None  # file of the intermediate dependency, if transitive


@dataclass
class Report:
    changed: list[ChangedSymbol] = field(default_factory=list)
    direct_dependents: list[Dependent] = field(default_factory=list)
    potentially_affected: list[Dependent] = field(default_factory=list)
    relevant_tests: list[Dependent] = field(default_factory=list)
    confidence: str = HIGH
    confidence_reasons: list[str] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)  # non-Python changes


def _downgrade(report: Report, level: str, reason: str) -> None:
    if _LEVEL_ORDER[level] > _LEVEL_ORDER[report.confidence]:
        report.confidence = level
    if reason not in report.confidence_reasons:
        report.confidence_reasons.append(reason)


def analyze(root: Path, diffs: list[FileDiff], staged: bool = False) -> Report:
    report = Report()
    overrides: dict[str, str | None] = {}
    py_diffs: list[FileDiff] = []
    for diff in diffs:
        if analyzer_for(diff.path) is None:
            report.ignored_files.append(diff.path)
            continue
        py_diffs.append(diff)
        # Analyze the version of the file the diff range ends at (HEAD or
        # index), not whatever currently sits in the working tree.
        overrides[diff.path] = None if diff.is_deleted else file_content(root, diff.path, staged)

    graph = build_graph(root, overrides)
    changed_modules: dict[str, FileDiff] = {}

    for diff in py_diffs:
        analyzer = analyzer_for(diff.path)
        assert analyzer is not None
        module = graph.module_for_path(diff.path) or analyzer.module_name(diff.path)
        if module is None:
            report.ignored_files.append(diff.path)
            continue
        changed_modules[module] = diff
        if diff.is_deleted:
            report.changed.append(ChangedSymbol(diff.path, None, "module"))
            continue
        source = overrides.get(diff.path)
        spans = analyzer.map_lines_to_symbols(source, diff.changed_lines) if source else None
        if source is None or spans is None:
            report.changed.append(ChangedSymbol(diff.path, None, "module"))
            _downgrade(report, LOW, f"changed file {diff.path} could not be parsed")
            continue
        if spans:
            for span in sorted(spans, key=lambda s: s.start):
                report.changed.append(ChangedSymbol(diff.path, span.qualname, span.kind))
        else:
            report.changed.append(ChangedSymbol(diff.path, None, "module"))

    changed_symbol_names = {
        (graph.module_for_path(c.file) or "", c.qualname.split(".")[0])
        for c in report.changed
        if c.qualname
    }

    _collect_dependents(graph, changed_modules, changed_symbol_names, report)
    _assess_confidence(graph, changed_modules, report)
    return report


def _collect_dependents(
    graph: ImportGraph,
    changed_modules: dict[str, FileDiff],
    changed_symbol_names: set[tuple[str, str]],
    report: Report,
) -> None:
    distance: dict[str, int] = {}
    via: dict[str, str] = {}
    frontier = list(changed_modules)
    for m in frontier:
        distance[m] = 0
    while frontier:
        nxt: list[str] = []
        for module in frontier:
            diff = changed_modules.get(module)
            dependents = (
                graph.raw_dependents_of(module)
                if diff is not None and diff.is_deleted
                else graph.dependents_of(module)
            )
            for dep in sorted(dependents):
                if dep in distance:
                    continue
                distance[dep] = distance[module] + 1
                via[dep] = module
                nxt.append(dep)
        frontier = nxt

    for module, dist in distance.items():
        if dist == 0:
            continue
        path = graph.path_for_module(module) or module
        called = sorted(
            sym
            for target, sym in graph.calls.get(module, set())
            if (target, sym) in changed_symbol_names
        )
        dep = Dependent(
            file=path,
            module=module,
            distance=dist,
            calls_changed_symbol=bool(called),
            called_symbols=tuple(called),
            via=graph.path_for_module(via[module]) if via.get(module) else None,
        )
        analyzer = analyzer_for(path)
        if analyzer is not None and analyzer.is_test_file(path):
            report.relevant_tests.append(dep)
        elif dist == 1:
            report.direct_dependents.append(dep)
        else:
            report.potentially_affected.append(dep)

    report.direct_dependents.sort(key=lambda d: (not d.calls_changed_symbol, d.file))
    report.potentially_affected.sort(key=lambda d: (d.distance, d.file))
    report.relevant_tests.sort(key=lambda d: (d.distance, d.file))


def _assess_confidence(
    graph: ImportGraph, changed_modules: dict[str, FileDiff], report: Report
) -> None:
    affected = set(changed_modules)
    affected.update(d.module for d in report.direct_dependents)
    affected.update(d.module for d in report.potentially_affected)
    affected.update(d.module for d in report.relevant_tests)

    for module in sorted(changed_modules):
        for star_importer in sorted(graph.star_importers_of(module)):
            path = graph.path_for_module(star_importer) or star_importer
            _downgrade(
                report,
                MEDIUM,
                f"{path} star-imports a changed module; symbol-level impact is hidden",
            )

    for module in sorted(graph.dynamic_importers()):
        path = graph.path_for_module(module) or module
        _downgrade(
            report,
            MEDIUM,
            f"{path} uses dynamic imports (importlib/__import__); dependents may be missed",
        )

    for path, _err in sorted(graph.unparseable.items()):
        if graph.module_for_path(path) in changed_modules:
            continue  # already reported as Low above
        _downgrade(report, MEDIUM, f"{path} could not be parsed; its imports are unknown")

    for module in sorted(affected):
        facts = graph.facts.get(module)
        if facts is not None and facts.unresolved_relative_import:
            _downgrade(
                report, LOW, f"{facts.path} has a relative import that could not be resolved"
            )
