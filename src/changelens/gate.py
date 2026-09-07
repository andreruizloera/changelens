"""Turn a Report into a pass/fail decision, so CI can gate on blast radius.

A condition is written the way the failure reads, not the way success
reads: `--fail-on "affected>20"` means "fail when more than 20 files are
affected". The gate fails when any single condition is true.

A condition can compare against a saved baseline instead of a fixed number:
`--fail-on "affected>baseline+10"` means "fail when this run is more than 10
files wider than the baseline", which is the condition an established
repository can actually keep switched on. See baseline.py.

The offset can also be a percentage, `--fail-on "affected>baseline+25%"`,
because a fixed file count is the wrong unit at both ends of the repository
size range: ten files is noise in a four-thousand-file repository and a
rewrite in a forty-file one.

Five rules here are judgement calls and are documented rather than hidden:

- Confidence compares by RISK, so high < medium < low. `confidence>=medium`
  trips on a Medium or a Low report; `confidence=low` trips only on Low.
- The gate is SKIPPED, not passed, when the diff contains no Python
  changes. Every count would be zero, and a condition like `tests=0` would
  otherwise fire on a documentation-only pull request.
- A relative condition with no baseline is an ERROR, never a pass. A gate
  that quietly waves a branch through because a file was missing is a rubber
  stamp.
- `confidence>baseline` is allowed but `confidence>baseline+1` is not: a
  step on a three-level risk scale is not a quantity worth writing gates in.
- A percentage is a percentage OF THE BASELINE VALUE, not of the repository,
  and it is evaluated in exact integer arithmetic (both sides scaled by 100)
  so no rounding rule has to be invented or remembered. A run sitting exactly
  on the threshold does not trip `>`.

This module is pure: it reads a Report and returns data. Rendering lives in
report.py.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from changelens.baseline import Baseline, FileSets, Spec
from changelens.impact import HIGH, LOW, MEDIUM, Report

# Ordered by risk, not by name, so comparisons read as "at least this bad".
CONFIDENCE_RISK = {HIGH: 0, MEDIUM: 1, LOW: 2}
_LEVEL_BY_WORD = {level.lower(): level for level in CONFIDENCE_RISK}

CONFIDENCE = "confidence"
AFFECTED = "affected"

# name -> what it counts, in the order they are listed to the user
NUMERIC_METRICS: dict[str, str] = {
    "changed": "changed Python files",
    "direct": "files that import a changed module directly",
    "transitive": "files reached at distance 2 or more",
    "tests": "test files reached from the change",
    AFFECTED: "all downstream files (direct + transitive + tests)",
    "distance": "longest import hop from a change to a dependent",
}
METRIC_NAMES: tuple[str, ...] = (*NUMERIC_METRICS, CONFIDENCE)

# Metrics that count a set of files, and can therefore say WHICH files
# entered the radius since a baseline. `distance` is a hop count and
# `confidence` is a label, so neither names files.
FILE_BUCKETS: tuple[str, ...] = ("changed", "direct", "transitive", "tests")
_AFFECTED_BUCKETS: tuple[str, ...] = ("direct", "transitive", "tests")

_OPS: dict[str, Callable[[int, int], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

_EXPRESSION = re.compile(r"^\s*([A-Za-z_]+)\s*(>=|<=|==|!=|=|>|<)\s*(.+?)\s*$")
_BARE_NAME = re.compile(r"^\s*([A-Za-z_]+)\s*$")
_BASELINE_VALUE = re.compile(r"^baseline\s*(?:([+-])\s*(\d+)\s*(%?))?$", re.IGNORECASE)

# Both sides of a percentage comparison are multiplied by this, which keeps the
# arithmetic in integers: no float, no rounding rule, no drift at the boundary.
PERCENT_SCALE = 100


class GateError(ValueError):
    """A --fail-on expression that could not be parsed or could not be evaluated."""


@dataclass(frozen=True)
class Condition:
    expression: str  # as the user wrote it
    metric: str
    op: str
    value: int  # absolute target; the offset from the baseline when relative
    value_text: str  # what the value should look like when printed back
    relative: bool = False  # compare against a saved baseline, not a constant
    percent: bool = False  # the offset is percent OF THE BASELINE, not a count


@dataclass(frozen=True)
class ConditionResult:
    condition: Condition
    actual: int
    actual_text: str
    tripped: bool
    baseline: int | None = None  # the baseline's value, for a relative condition
    baseline_text: str | None = None
    entered: tuple[str, ...] = ()  # files this metric gained since the baseline
    threshold_text: str | None = None  # the count a percentage worked out to

    @property
    def delta(self) -> int | None:
        return None if self.baseline is None else self.actual - self.baseline


@dataclass(frozen=True)
class GateResult:
    results: tuple[ConditionResult, ...]
    metrics: dict[str, int]
    confidence: str
    skipped: bool
    files: FileSets = field(default_factory=dict)
    baseline: Baseline | None = None

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

    @property
    def confidence_drift(self) -> str | None:
        """The baseline's confidence, when this run did not read at the same level.

        A comparison between two reports of different confidence is partly a
        comparison of how much each one could see. Growth can be an edge
        becoming visible, and a shrink can be an edge going dark.
        """
        if self.baseline is None or self.skipped:
            return None
        if self.baseline.confidence == self.confidence:
            return None
        if not any(r.condition.relative for r in self.results):
            return None
        return self.baseline.confidence


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
            '"affected>20", "affected>baseline+10", or "confidence=low"'
        )

    metric = match.group(1).lower()
    op = "==" if match.group(2) == "=" else match.group(2)
    raw = match.group(3)

    if metric != CONFIDENCE and metric not in NUMERIC_METRICS:
        raise GateError(
            f"unknown metric {metric!r} in --fail-on {expression!r}; "
            f"valid metrics are {', '.join(METRIC_NAMES)}"
        )

    relative = _BASELINE_VALUE.match(raw)
    if relative is not None:
        sign, digits, mark = relative.group(1), relative.group(2), relative.group(3)
        percent = mark == "%"
        if metric == CONFIDENCE:
            if digits is not None:
                raise GateError(
                    f"confidence compares against the baseline itself, not an offset from "
                    f'it, in --fail-on {expression!r}; write "confidence>baseline" to fail '
                    "when this run is less trustworthy than the baseline"
                )
            return Condition(expression, metric, op, 0, "baseline", relative=True)
        offset = 0 if digits is None else int(digits) * (-1 if sign == "-" else 1)
        if percent and offset < -PERCENT_SCALE:
            raise GateError(
                f"a baseline cannot shrink by more than 100%, so {raw!r} in --fail-on "
                f"{expression!r} describes a threshold below zero"
            )
        text = "baseline" if digits is None else f"baseline{sign}{digits}{mark}"
        return Condition(expression, metric, op, offset, text, relative=True, percent=percent)

    if raw.lower().startswith("baseline"):
        # Close enough to the baseline form that the generic "whole number"
        # message would send the reader looking in the wrong place.
        raise GateError(
            f"cannot read the baseline offset {raw!r} in --fail-on {expression!r}; write it "
            'as "baseline", "baseline+10" (ten more files), or "baseline+25%" (a quarter '
            "wider than the baseline). Offsets are whole numbers."
        )

    if metric == CONFIDENCE:
        level = _LEVEL_BY_WORD.get(raw.lower())
        if level is None:
            raise GateError(
                f"unknown confidence level {raw!r} in --fail-on {expression!r}; "
                "use high, medium, low, or baseline"
            )
        return Condition(expression, metric, op, CONFIDENCE_RISK[level], level.lower())

    try:
        value = int(raw)
    except ValueError:
        raise GateError(
            f"{metric} compares against a whole number or a baseline expression, "
            f"not {raw!r}, in --fail-on {expression!r}"
        ) from None
    if value < 0:
        raise GateError(f"{metric} cannot be negative in --fail-on {expression!r}")
    return Condition(expression, metric, op, value, str(value))


def parse_conditions(expressions: Iterable[str]) -> list[Condition]:
    return [parse_condition(e) for e in expressions]


def needs_baseline(conditions: Iterable[Condition]) -> bool:
    return any(c.relative for c in conditions)


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
        AFFECTED: len(dependents),
        "distance": max((d.distance for d in dependents), default=0),
    }


def file_sets_of(report: Report) -> FileSets:
    """The files behind each counting metric, for baseline comparison.

    `affected` is not stored: it is the union of the other three, derived on
    demand by files_for so the two can never disagree.
    """
    return {
        "changed": tuple(sorted({c.file for c in report.changed})),
        "direct": tuple(sorted(d.file for d in report.direct_dependents)),
        "transitive": tuple(sorted(d.file for d in report.potentially_affected)),
        "tests": tuple(sorted(d.file for d in report.relevant_tests)),
    }


def files_for(sets: FileSets, metric: str) -> tuple[str, ...] | None:
    """The files one metric counted, or None when they are not knowable.

    None covers both a metric that counts no files (distance, confidence) and
    a baseline that did not record the list. Those are the same answer here,
    and both are different from an empty list: a baseline with no `files`
    entry must not make every file in this run look newly arrived.
    """
    if metric in FILE_BUCKETS:
        return sets.get(metric)
    if metric == AFFECTED:
        if any(bucket not in sets for bucket in _AFFECTED_BUCKETS):
            return None
        return tuple(sorted({f for bucket in _AFFECTED_BUCKETS for f in sets[bucket]}))
    return None


def baseline_from(
    report: Report,
    spec: Spec,
    head: str | None = None,
    created: str | None = None,
    version: str | None = None,
) -> Baseline:
    """The saveable snapshot of one report."""
    return Baseline(
        metrics=metrics_of(report),
        confidence=report.confidence,
        spec=spec,
        files=file_sets_of(report),
        head=head,
        created=created,
        version=version,
    )


def _baseline_value(baseline: Baseline, condition: Condition) -> tuple[int, str]:
    if condition.metric == CONFIDENCE:
        rank = CONFIDENCE_RISK.get(baseline.confidence)
        if rank is None:
            raise GateError(
                f"the baseline records confidence {baseline.confidence!r}, which is not "
                f"one of {', '.join(_LEVEL_BY_WORD)}; re-save it with --save-baseline"
            )
        return rank, baseline.confidence.lower()
    if condition.metric not in baseline.metrics:
        raise GateError(
            f"the baseline has no {condition.metric!r} metric, so --fail-on "
            f"{condition.expression!r} cannot be evaluated; re-save it with --save-baseline"
        )
    value = baseline.metrics[condition.metric]
    return value, str(value)


def percent_threshold(baseline_value: int, offset: int) -> int:
    """The right-hand side of a percentage comparison, scaled by PERCENT_SCALE.

    Scaling instead of dividing keeps this exact: `affected>baseline+25%`
    against a baseline of 6 compares 100*actual against 750, so 7 passes and 8
    trips, and no rounding rule has to be chosen. A baseline of 0 gives a
    threshold of 0, which is the honest answer: no percentage of nothing is
    room to grow, so any growth from an empty radius trips. Nothing here
    divides by the baseline, so a zero baseline is arithmetic, not an error.
    """
    return baseline_value * (PERCENT_SCALE + offset)


def format_scaled(scaled: int) -> str:
    """A PERCENT_SCALE-scaled threshold as an exact decimal, for reporting."""
    sign = "-" if scaled < 0 else ""
    whole, fraction = divmod(abs(scaled), PERCENT_SCALE)
    if fraction == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{fraction:02d}".rstrip("0")


def check_against(conditions: Iterable[Condition], baseline: Baseline) -> None:
    """Raise GateError if the baseline cannot answer these conditions.

    Called before the repository is parsed, so an unusable baseline costs
    milliseconds instead of a full analysis. evaluate() checks again, since
    it is the function that must not be fooled.
    """
    for condition in conditions:
        if condition.relative:
            _baseline_value(baseline, condition)


def evaluate(
    report: Report,
    conditions: Sequence[Condition],
    baseline: Baseline | None = None,
) -> GateResult:
    metrics = metrics_of(report)
    sets = file_sets_of(report)
    skipped = not report.changed
    results: list[ConditionResult] = []
    for condition in conditions:
        if condition.metric == CONFIDENCE:
            actual = CONFIDENCE_RISK[report.confidence]
            actual_text = report.confidence.lower()
        else:
            actual = metrics[condition.metric]
            actual_text = str(actual)

        base_value: int | None = None
        base_text: str | None = None
        threshold_text: str | None = None
        entered: tuple[str, ...] = ()
        # A percentage comparison runs with both sides multiplied by
        # PERCENT_SCALE, so the same integer operators decide both forms.
        scale = 1
        target = condition.value
        if condition.relative:
            if baseline is None:
                raise GateError(
                    f"--fail-on {condition.expression!r} compares against a baseline, "
                    "and none was loaded"
                )
            base_value, base_text = _baseline_value(baseline, condition)
            if condition.percent:
                scale = PERCENT_SCALE
                target = percent_threshold(base_value, condition.value)
                threshold_text = format_scaled(target)
            else:
                target = base_value + condition.value
            now = files_for(sets, condition.metric)
            before = files_for(baseline.files, condition.metric)
            if now is not None and before is not None:
                entered = tuple(f for f in now if f not in set(before))

        tripped = False if skipped else _OPS[condition.op](actual * scale, target)
        results.append(
            ConditionResult(
                condition=condition,
                actual=actual,
                actual_text=actual_text,
                tripped=tripped,
                baseline=base_value,
                baseline_text=base_text,
                entered=entered,
                threshold_text=threshold_text,
            )
        )
    return GateResult(
        results=tuple(results),
        metrics=metrics,
        confidence=report.confidence,
        skipped=skipped,
        files=sets,
        baseline=baseline,
    )
