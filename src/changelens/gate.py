"""Turn a Report into a pass/fail decision, so CI can gate on blast radius.

A condition is written the way the failure reads, not the way success
reads: `--fail-on "affected>20"` means "fail when more than 20 files are
affected". The gate fails when any single condition is true.

Two rules here are judgement calls and are documented rather than hidden:

- Confidence compares by RISK, so high < medium < low. `confidence>=medium`
  trips on a Medium or a Low report; `confidence=low` trips only on Low.
- The gate is SKIPPED, not passed, when the diff contains no Python
  changes. Every count would be zero, and a condition like `tests=0` would
  otherwise fire on a documentation-only pull request.

This module is pure: it reads a Report and returns data. Rendering lives in
report.py.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from changelens.impact import HIGH, LOW, MEDIUM, Report

# Ordered by risk, not by name, so comparisons read as "at least this bad".
CONFIDENCE_RISK = {HIGH: 0, MEDIUM: 1, LOW: 2}
_LEVEL_BY_WORD = {level.lower(): level for level in CONFIDENCE_RISK}

CONFIDENCE = "confidence"

# name -> what it counts, in the order they are listed to the user
NUMERIC_METRICS: dict[str, str] = {
    "changed": "changed Python files",
    "direct": "files that import a changed module directly",
    "transitive": "files reached at distance 2 or more",
    "tests": "test files reached from the change",
    "affected": "all downstream files (direct + transitive + tests)",
    "distance": "longest import hop from a change to a dependent",
}
METRIC_NAMES: tuple[str, ...] = (*NUMERIC_METRICS, CONFIDENCE)

_OPS: dict[str, Callable[[int, int], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

_EXPRESSION = re.compile(r"^\s*([A-Za-z_]+)\s*(>=|<=|==|!=|=|>|<)\s*(\S+)\s*$")
_BARE_NAME = re.compile(r"^\s*([A-Za-z_]+)\s*$")


class GateError(ValueError):
    """A --fail-on expression that could not be parsed."""


@dataclass(frozen=True)
class Condition:
    expression: str  # as the user wrote it
    metric: str
    op: str
    value: int  # confidence values are stored as their risk rank
    value_text: str  # what the value should look like when printed back


@dataclass(frozen=True)
class ConditionResult:
    condition: Condition
    actual: int
    actual_text: str
    tripped: bool


@dataclass(frozen=True)
class GateResult:
    results: tuple[ConditionResult, ...]
    metrics: dict[str, int]
    confidence: str
    skipped: bool

    @property
    def failed(self) -> bool:
        return any(r.tripped for r in self.results)

    @property
    def understated(self) -> bool:
        """True when a numeric condition passed on a degraded report.

        Star imports, dynamic imports, and unparseable files hide edges, so a
        Medium or Low report under-counts. A numeric all-clear read off one is
        worth less than the same all-clear read off a High report, and saying
        so is the difference between a gate and a rubber stamp.
        """
        if self.skipped or self.confidence == HIGH:
            return False
        return any(not r.tripped and r.condition.metric != CONFIDENCE for r in self.results)


def parse_condition(expression: str) -> Condition:
    """Parse one --fail-on expression, or raise GateError with a usable message."""
    match = _EXPRESSION.match(expression)
    if match is None:
        bare = _BARE_NAME.match(expression)
        if bare is not None and bare.group(1).lower() in METRIC_NAMES:
            raise GateError(
                f"--fail-on {expression!r} has no comparison; if you wrote "
                f'--fail-on {expression}>10 the shell ate the ">" as a redirect. '
                f'Quote it: --fail-on "{expression}>10"'
            )
        raise GateError(
            f"cannot parse --fail-on {expression!r}; expected something like "
            '"affected>20" or "confidence=low"'
        )

    metric = match.group(1).lower()
    op = "==" if match.group(2) == "=" else match.group(2)
    raw = match.group(3)

    if metric == CONFIDENCE:
        level = _LEVEL_BY_WORD.get(raw.lower())
        if level is None:
            raise GateError(
                f"unknown confidence level {raw!r} in --fail-on {expression!r}; "
                "use high, medium, or low"
            )
        return Condition(expression, metric, op, CONFIDENCE_RISK[level], level.lower())

    if metric not in NUMERIC_METRICS:
        raise GateError(
            f"unknown metric {metric!r} in --fail-on {expression!r}; "
            f"valid metrics are {', '.join(METRIC_NAMES)}"
        )
    try:
        value = int(raw)
    except ValueError:
        raise GateError(
            f"{metric} compares against a whole number, not {raw!r}, in --fail-on {expression!r}"
        ) from None
    if value < 0:
        raise GateError(f"{metric} cannot be negative in --fail-on {expression!r}")
    return Condition(expression, metric, op, value, str(value))


def parse_conditions(expressions: Iterable[str]) -> list[Condition]:
    return [parse_condition(e) for e in expressions]


def metrics_of(report: Report) -> dict[str, int]:
    """The numeric metrics a gate can compare against.

    `affected` is a count of downstream FILES, not of edges: a module lands in
    exactly one of the three buckets, so the sum is already distinct. Changed
    files are not counted as affected by themselves.
    """
    dependents = [
        *report.direct_dependents,
        *report.potentially_affected,
        *report.relevant_tests,
    ]
    return {
        "changed": len({c.file for c in report.changed}),
        "direct": len(report.direct_dependents),
        "transitive": len(report.potentially_affected),
        "tests": len(report.relevant_tests),
        "affected": len(dependents),
        "distance": max((d.distance for d in dependents), default=0),
    }


def evaluate(report: Report, conditions: Sequence[Condition]) -> GateResult:
    metrics = metrics_of(report)
    skipped = not report.changed
    results: list[ConditionResult] = []
    for condition in conditions:
        if condition.metric == CONFIDENCE:
            actual = CONFIDENCE_RISK[report.confidence]
            actual_text = report.confidence.lower()
        else:
            actual = metrics[condition.metric]
            actual_text = str(actual)
        tripped = False if skipped else _OPS[condition.op](actual, condition.value)
        results.append(ConditionResult(condition, actual, actual_text, tripped))
    return GateResult(
        results=tuple(results),
        metrics=metrics,
        confidence=report.confidence,
        skipped=skipped,
    )
