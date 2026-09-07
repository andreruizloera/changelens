"""Render an impact Report as terminal text, JSON, or a mermaid flowchart."""

from __future__ import annotations

import json
import re
import textwrap

from changelens.gate import CONFIDENCE, ConditionResult, GateResult, metrics_of
from changelens.impact import HIGH, Dependent, Report

# 2 added the always-present "metrics" object and the "gate" object that
# appears when --fail-on is used. Everything from version 1 is unchanged.
# 3 added the "baseline" object and, on a condition that compares against a
# baseline, its "baseline", "delta", and "entered" keys. A condition that
# compares against a constant is byte-for-byte what version 2 emitted.
# 4 added "threshold" on a condition whose offset is a percentage, the count
# that percentage worked out to. Conditions written without a percentage are
# byte-for-byte what version 3 emitted.
# 5 added "provenance" inside the "baseline" object: where the baseline's
# recorded commit sits relative to this run's HEAD.
JSON_SCHEMA_VERSION = 5

# How a metric's new files should be described, per metric.
_ENTERED_PHRASE = {
    "changed": "changed here and not in the baseline",
    "direct": "became direct dependents since the baseline",
    "transitive": "became transitive dependents since the baseline",
    "tests": "became relevant tests since the baseline",
    "affected": "entered the radius since the baseline",
}
_ENTERED_SHOWN = 5
_WARNING_WIDTH = 78

# What to do about a baseline whose provenance did not check out. The gate
# still reports its numbers, so the reader needs the two ways out of it.
_PROVENANCE_ADVICE = (
    "Re-save the baseline from this branch, or pass --require-baseline-ancestor "
    "to make this an error instead of a warning."
)


def render_terminal(report: Report, gate: GateResult | None = None) -> str:
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

    if gate is not None:
        lines.append("")
        lines.append(render_gate(gate))
    return "\n".join(lines)


def _delta_text(result: ConditionResult) -> str:
    """How the actual moved from the baseline, in the units of the metric."""
    delta = result.delta
    assert delta is not None
    if result.condition.metric == CONFIDENCE:
        # Confidence is a three-level risk scale, so a signed number would
        # read as a quantity it is not. Higher rank means more risk.
        return "worse" if delta > 0 else "better" if delta < 0 else "same"
    return "no change" if delta == 0 else f"{delta:+d}"


def _warning_lines(text: str) -> list[str]:
    """A warning wrapped under a hanging "Warning:" label.

    Hyphens do not break: a flag name split across two lines is a flag name
    someone pastes into a shell wrong.
    """
    return textwrap.wrap(
        text,
        width=_WARNING_WIDTH,
        initial_indent="  Warning: ",
        subsequent_indent="           ",
        break_on_hyphens=False,
        break_long_words=False,
    )


def render_gate(gate: GateResult) -> str:
    """The gate verdict as its own block, for the terminal or for stderr."""
    if gate.skipped:
        return "Gate: skipped (no Python changes to measure)"

    verdict = "failed" if gate.failed else "passed"
    # An unverified baseline does not change the verdict, so it qualifies it:
    # a reader must not be able to quote "Gate: passed" out of this block
    # without also quoting the reason it may mean nothing.
    if (unverified := gate.unverified) is not None:
        verdict += f" ({unverified.label})"
    lines = [f"Gate: {verdict}"]
    width = max(len(r.condition.expression) for r in gate.results) if gate.results else 0
    for result in gate.results:
        mark = "FAIL" if result.tripped else "ok  "
        expression = result.condition.expression.ljust(width)
        metric = result.condition.metric
        actual = f"actual: {metric} = {result.actual_text}"
        if result.baseline_text is not None:
            actual += f", baseline {result.baseline_text}, {_delta_text(result)}"
        if result.threshold_text is not None:
            # A percentage is written in one unit and decided in another, so
            # the count it worked out to is the number the reader needs.
            actual += f", threshold {result.threshold_text}"
        lines.append(f"  {mark}  {expression}  ({actual})")
        # Which files moved is the answer a reviewer actually needs, and it
        # is only worth the space when the condition tripped upward.
        if result.tripped and result.entered and (result.delta or 0) > 0:
            phrase = _ENTERED_PHRASE.get(metric, "are new since the baseline")
            noun = "file" if len(result.entered) == 1 else "files"
            lines.append(f"        {len(result.entered)} {noun} {phrase}:")
            lines.extend(f"          {path}" for path in result.entered[:_ENTERED_SHOWN])
            if len(result.entered) > _ENTERED_SHOWN:
                lines.append(f"          ... and {len(result.entered) - _ENTERED_SHOWN} more")
    if unverified is not None:
        lines.extend(_warning_lines(f"{unverified.describe()}. {_PROVENANCE_ADVICE}"))
    if (drifted := gate.confidence_drift) is not None:
        lines.append(
            f"  Note: the baseline read at {drifted} confidence and this run reads at "
            f"{gate.confidence},"
        )
        lines.append("        so part of any movement here is edges becoming visible or going")
        lines.append("        dark rather than impact changing.")
    if gate.understated:
        lines.append(
            f"  Note: confidence is {gate.confidence}, so the counts this gate read can be"
        )
        lines.append("        lower than reality; a passing number is weaker evidence here.")
    return "\n".join(lines)


def render_json(report: Report, gate: GateResult | None = None) -> str:
    payload: dict[str, object] = {
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
        "metrics": metrics_of(report),
    }
    if gate is not None:
        payload["gate"] = _gate_dict(gate)
    return json.dumps(payload, indent=2)


def _gate_dict(gate: GateResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "failed": gate.failed,
        "skipped": gate.skipped,
        "understated": gate.understated,
        "conditions": [_condition_dict(r) for r in gate.results],
    }
    if gate.baseline is not None:
        baseline: dict[str, object] = {
            "spec": {"ref": gate.baseline.spec.ref, "staged": gate.baseline.spec.staged},
            "head": gate.baseline.head,
            "created": gate.baseline.created,
            "confidence": gate.baseline.confidence.lower(),
            "metrics": dict(gate.baseline.metrics),
        }
        if gate.provenance is not None:
            baseline["provenance"] = {
                "status": gate.provenance.status,
                "verified": gate.provenance.ok,
                "head": gate.provenance.current,
            }
        payload["baseline"] = baseline
    return payload


def _condition_dict(result: ConditionResult) -> dict[str, object]:
    is_confidence = result.condition.metric == CONFIDENCE
    payload: dict[str, object] = {
        "expression": result.condition.expression,
        "metric": result.condition.metric,
        "operator": result.condition.op,
        "value": result.condition.value_text,
        "actual": result.actual_text if is_confidence else result.actual,
        "tripped": result.tripped,
    }
    # Only a relative condition carries these, so a constant-threshold gate
    # emits exactly what schema version 2 emitted.
    if result.condition.relative:
        payload["baseline"] = result.baseline_text if is_confidence else result.baseline
        payload["delta"] = _delta_text(result) if is_confidence else result.delta
        payload["entered"] = list(result.entered)
    if result.threshold_text is not None:
        payload["threshold"] = result.threshold_text
    return payload


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
