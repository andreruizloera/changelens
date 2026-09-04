"""Python implementation of the LanguageAnalyzer contract.

Everything here is derived from the standard library ast module. No code
is imported or executed. Call resolution is deliberately conservative:
we only resolve calls whose target can be traced through explicit import
bindings in the same file (see _CallCollector), and the impact layer
labels those results as approximate.
"""

from __future__ import annotations

import ast

from changelens.analyzer import ModuleFacts, SymbolSpan


def _dotted(node: ast.expr) -> str | None:
    """Flatten a Name/Attribute chain like a.b.c into 'a.b.c', else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str | None:
    """Resolve a relative import to an absolute dotted name, or None."""
    parts = module.split(".")
    # In a package's __init__, level 1 refers to the package itself;
    # in a plain module, level 1 refers to the containing package.
    drop = level - 1 if is_package else level
    if drop > len(parts):
        return None
    base = parts[: len(parts) - drop]
    if target:
        base = [*base, *target.split(".")]
    if not base:
        return None
    return ".".join(base)


_DYNAMIC_IMPORT_CALLS = {
    "importlib.import_module",
    "importlib.util.spec_from_file_location",
    "__import__",
}


class _FactCollector(ast.NodeVisitor):
    def __init__(self, facts: ModuleFacts) -> None:
        self.facts = facts
        # local name -> absolute dotted target it is bound to
        self.aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.facts.imports.add(alias.name)
            if alias.asname:
                self.aliases[alias.asname] = alias.name
            else:
                # `import a.b.c` binds the root name `a`
                root = alias.name.split(".")[0]
                self.aliases[root] = root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            base = _resolve_relative(
                self.facts.module, self.facts.is_package, node.level, node.module
            )
            if base is None:
                self.facts.unresolved_relative_import = True
                self.generic_visit(node)
                return
        else:
            base = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self.facts.star_imports.add(base)
                continue
            self.facts.from_imports.add((base, alias.name))
            self.aliases[alias.asname or alias.name] = f"{base}.{alias.name}"
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        chain = _dotted(node.func)
        if chain:
            if chain in _DYNAMIC_IMPORT_CALLS:
                self.facts.uses_dynamic_import = True
            else:
                root, _, rest = chain.partition(".")
                target = self.aliases.get(root)
                if target is not None:
                    self.facts.calls.add(f"{target}.{rest}" if rest else target)
        self.generic_visit(node)


class PythonAnalyzer:
    language = "python"
    extensions: tuple[str, ...] = (".py",)

    def module_name(self, relpath: str) -> str | None:
        parts = relpath[: -len(".py")].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        # Support the common src/ layout by stripping the leading src dir.
        if len(parts) > 1 and parts[0] == "src":
            parts = parts[1:]
        if not parts or not all(p.isidentifier() for p in parts):
            return None
        return ".".join(parts)

    def parse_file(self, source: str, relpath: str, module: str, is_package: bool) -> ModuleFacts:
        facts = ModuleFacts(module=module, path=relpath, is_package=is_package)
        try:
            tree = ast.parse(source, filename=relpath)
        except (SyntaxError, ValueError) as exc:
            facts.parse_error = str(exc)
            return facts
        _FactCollector(facts).visit(tree)
        return facts

    def map_lines_to_symbols(self, source: str, lines: set[int]) -> list[SymbolSpan] | None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return None
        spans: list[tuple[int, SymbolSpan]] = []  # (depth, span)

        def walk(node: ast.AST, prefix: str, depth: int) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    qual = f"{prefix}{child.name}"
                    start = min(
                        [child.lineno] + [d.lineno for d in child.decorator_list],
                    )
                    end = child.end_lineno or child.lineno
                    kind = "class" if isinstance(child, ast.ClassDef) else "function"
                    spans.append((depth, SymbolSpan(qual, kind, start, end)))
                    walk(child, f"{qual}.", depth + 1)
                else:
                    walk(child, prefix, depth)

        walk(tree, "", 0)
        hits: dict[str, SymbolSpan] = {}
        for line in sorted(lines):
            best: tuple[int, SymbolSpan] | None = None
            for depth, span in spans:
                if span.start <= line <= span.end and (best is None or depth > best[0]):
                    best = (depth, span)
            if best is not None:
                hits.setdefault(best[1].qualname, best[1])
        return list(hits.values())

    def is_test_file(self, relpath: str) -> bool:
        parts = relpath.split("/")
        name = parts[-1]
        in_test_dir = any(p in ("tests", "test") for p in parts[:-1])
        return in_test_dir or name.startswith("test_") or name.endswith("_test.py")
