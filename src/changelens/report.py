"""Render an impact Report as terminal text, JSON, or a mermaid flowchart."""

from __future__ import annotations

import json
import re

from changelens.impact import HIGH, Dependent, Report

JSON_SCHEMA_VERSION = 1


def render_terminal(report: Report) -> str:
    lines = ["Change blast radius", "-------------------", ""]

    lines.append("Changed:")
    if report.changed:
        lines.extend(f"  {c.display}" for c in report.changed)
    else:
        lines.append("  (no Python changes in this range)")

    def section(title: str, deps: list[Dependent], annotate: bool) -> None:
        lines.append("")
        lines.append(f"{title}:")
        if not deps:
            lines.append("  (none found)")
            return
        for dep in deps:
            note = ""
            if annotate and dep.calls_changed_symbol:
                note = f"  (calls {', '.join(dep.called_symbols)})"
            elif dep.distance > 1 and dep.via:
                note = f"  (via {dep.via})"
            lines.append(f"  {dep.file}{note}")

    section("Direct dependents", report.direct_dependents, annotate=True)
    section("Potentially affected", report.potentially_affected, annotate=False)
    section("Relevant tests", report.relevant_tests, annotate=False)

    lines.append("")
    if report.confidence == HIGH:
        lines.append("Confidence: High (all changed symbols resolved; import graph is complete)")
    else:
        lines.append(f"Confidence: {report.confidence}")
        lines.extend(f"  - {reason}" for reason in report.confidence_reasons)

    if report.ignored_files:
        lines.append("")
        lines.append(
            f"Note: {len(report.ignored_files)} non-Python file(s) changed and were not analyzed:"
        )
        lines.extend(f"  {path}" for path in report.ignored_files)
    return "\n".join(lines)


def render_json(report: Report) -> str:
    payload = {
        "schema_version": JSON_SCHEMA_VERSION,
        "changed": [{"file": c.file, "symbol": c.qualname, "kind": c.kind} for c in report.changed],
        "direct_dependents": [_dep_dict(d) for d in report.direct_dependents],
        "potentially_affected": [_dep_dict(d) for d in report.potentially_affected],
        "relevant_tests": [_dep_dict(d) for d in report.relevant_tests],
        "confidence": {
            "level": report.confidence.lower(),
            "reasons": list(report.confidence_reasons),
        },
        "ignored_files": list(report.ignored_files),
    }
    return json.dumps(payload, indent=2)


def _dep_dict(dep: Dependent) -> dict[str, object]:
    return {
        "file": dep.file,
        "module": dep.module,
        "distance": dep.distance,
        "calls_changed_symbol": dep.calls_changed_symbol,
        "called_symbols": list(dep.called_symbols),
        "via": dep.via,
    }


def _node_id(prefix: str, name: str) -> str:
    return prefix + re.sub(r"[^a-zA-Z0-9]", "_", name)


def render_mermaid(report: Report) -> str:
    lines = ["flowchart TD"]
    changed_ids: dict[str, str] = {}
    for c in report.changed:
        nid = _node_id("c_", c.display)
        changed_ids[c.file] = nid
        lines.append(f'    {nid}["{c.display}"]:::changed')

    file_ids: dict[str, str] = dict(changed_ids)

    def add(dep: Dependent, prefix: str, cls: str) -> str:
        nid = _node_id(prefix, dep.file)
        if dep.file not in file_ids:
            file_ids[dep.file] = nid
            lines.append(f'    {nid}["{dep.file}"]:::{cls}')
        return file_ids[dep.file]

    for dep in report.direct_dependents:
        nid = add(dep, "d_", "dependent")
        parent = file_ids.get(dep.via or "")
        if parent:
            lines.append(f"    {nid} --> {parent}")
        else:
            for target in changed_ids.values():
                lines.append(f"    {nid} --> {target}")

    for dep in report.potentially_affected:
        nid = add(dep, "p_", "affected")
        parent = file_ids.get(dep.via or "")
        if parent:
            lines.append(f"    {nid} --> {parent}")

    for dep in report.relevant_tests:
        nid = add(dep, "t_", "test")
        parent = file_ids.get(dep.via or "")
        if parent and parent != nid:
            lines.append(f"    {nid} -.-> {parent}")
        else:
            for target in changed_ids.values():
                lines.append(f"    {nid} -.-> {target}")

    lines.append("    classDef changed fill:#b91c1c,color:#fff")
    lines.append("    classDef dependent fill:#c2410c,color:#fff")
    lines.append("    classDef affected fill:#a16207,color:#fff")
    lines.append("    classDef test fill:#15803d,color:#fff")
    return "\n".join(lines)
