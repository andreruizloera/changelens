from __future__ import annotations

import pytest

from changelens.gate import (
    GateError,
    evaluate,
    metrics_of,
    parse_condition,
    parse_conditions,
)
from changelens.impact import HIGH, LOW, MEDIUM, ChangedSymbol, Dependent, Report
from changelens.report import render_gate


def build_report(
    *,
    changed: int = 1,
    direct: int = 0,
    transitive: int = 0,
    tests: int = 0,
    max_distance: int = 2,
    confidence: str = HIGH,
) -> Report:
    report = Report(confidence=confidence)
    for i in range(changed):
        report.changed.append(ChangedSymbol(f"pkg/mod{i}.py", "run", "function"))
    for i in range(direct):
        report.direct_dependents.append(Dependent(f"app{i}.py", f"app{i}", 1))
    for i in range(transitive):
        report.potentially_affected.append(Dependent(f"far{i}.py", f"far{i}", max_distance))
    for i in range(tests):
        report.relevant_tests.append(Dependent(f"tests/test_{i}.py", f"tests.test_{i}", 2))
    return report


class TestParsing:
    @pytest.mark.parametrize(
        ("expression", "op", "value"),
        [
            ("affected>20", ">", 20),
            ("affected >= 20", ">=", 20),
            ("tests<1", "<", 1),
            ("tests <= 1", "<=", 1),
            ("direct==3", "==", 3),
            ("direct!=3", "!=", 3),
        ],
    )
    def test_operators(self, expression: str, op: str, value: int) -> None:
        condition = parse_condition(expression)
        assert (condition.op, condition.value) == (op, value)

    def test_single_equals_means_equality(self) -> None:
        assert parse_condition("tests=0").op == "=="

    def test_expression_is_kept_verbatim_for_reporting(self) -> None:
        assert parse_condition("  affected > 4 ").expression == "  affected > 4 "

    def test_metric_name_is_case_insensitive(self) -> None:
        assert parse_condition("AFFECTED>1").metric == "affected"

    def test_confidence_parses_to_its_risk_rank(self) -> None:
        assert parse_condition("confidence=low").value == 2
        assert parse_condition("confidence=high").value == 0

    def test_unknown_metric_lists_the_valid_ones(self) -> None:
        with pytest.raises(GateError) as exc:
            parse_condition("blastradius>3")
        assert "unknown metric 'blastradius'" in str(exc.value)
        assert "affected" in str(exc.value)
        assert "confidence" in str(exc.value)

    def test_unknown_confidence_level(self) -> None:
        with pytest.raises(GateError, match="unknown confidence level 'sometimes'"):
            parse_condition("confidence=sometimes")

    def test_non_integer_threshold(self) -> None:
        with pytest.raises(GateError, match="whole number"):
            parse_condition("affected>many")

    def test_negative_threshold(self) -> None:
        with pytest.raises(GateError, match="cannot be negative"):
            parse_condition("affected>-1")

    def test_bare_metric_blames_the_shell(self) -> None:
        # `--fail-on affected>20` unquoted: the shell redirects into a file
        # named 20 and changelens is handed the word "affected" alone.
        with pytest.raises(GateError) as exc:
            parse_condition("affected")
        assert "shell ate" in str(exc.value)
        assert '--fail-on "affected>10"' in str(exc.value)

    def test_bare_non_metric_gets_the_generic_message(self) -> None:
        with pytest.raises(GateError, match="cannot parse"):
            parse_condition("nonsense")

    def test_garbage_is_not_silently_accepted(self) -> None:
        for expression in ["", ">3", "affected>", "affected 3", "affected>3>4"]:
            with pytest.raises(GateError):
                parse_condition(expression)

    def test_parse_conditions_preserves_order(self) -> None:
        conditions = parse_conditions(["tests=0", "affected>5"])
        assert [c.metric for c in conditions] == ["tests", "affected"]


class TestMetrics:
    def test_affected_sums_the_three_downstream_buckets(self) -> None:
        report = build_report(direct=2, transitive=3, tests=4)
        assert metrics_of(report)["affected"] == 9

    def test_changed_counts_files_not_symbols(self) -> None:
        report = Report()
        report.changed.append(ChangedSymbol("pkg/a.py", "one", "function"))
        report.changed.append(ChangedSymbol("pkg/a.py", "two", "function"))
        report.changed.append(ChangedSymbol("pkg/b.py", None, "module"))
        assert metrics_of(report)["changed"] == 2

    def test_distance_is_the_longest_hop(self) -> None:
        report = build_report(direct=1, transitive=1, max_distance=4)
        assert metrics_of(report)["distance"] == 4

    def test_distance_of_a_change_nothing_imports_is_zero(self) -> None:
        assert metrics_of(build_report())["distance"] == 0

    def test_changed_files_are_not_counted_as_affected(self) -> None:
        assert metrics_of(build_report(changed=3))["affected"] == 0


class TestEvaluation:
    def test_condition_trips_when_true(self) -> None:
        gate = evaluate(build_report(direct=5), parse_conditions(["affected>4"]))
        assert gate.failed
        assert gate.results[0].actual == 5

    def test_condition_at_the_threshold_does_not_trip(self) -> None:
        gate = evaluate(build_report(direct=4), parse_conditions(["affected>4"]))
        assert not gate.failed

    def test_any_tripped_condition_fails_the_gate(self) -> None:
        gate = evaluate(build_report(direct=1), parse_conditions(["affected>99", "tests=0"]))
        assert [r.tripped for r in gate.results] == [False, True]
        assert gate.failed

    def test_confidence_compares_by_risk(self) -> None:
        conditions = parse_conditions(["confidence>=medium"])
        assert not evaluate(build_report(confidence=HIGH), conditions).failed
        assert evaluate(build_report(confidence=MEDIUM), conditions).failed
        assert evaluate(build_report(confidence=LOW), conditions).failed

    def test_exact_confidence_match(self) -> None:
        conditions = parse_conditions(["confidence=low"])
        assert not evaluate(build_report(confidence=MEDIUM), conditions).failed
        assert evaluate(build_report(confidence=LOW), conditions).failed

    def test_confidence_reports_the_word_not_the_rank(self) -> None:
        gate = evaluate(build_report(confidence=MEDIUM), parse_conditions(["confidence=low"]))
        assert gate.results[0].actual_text == "medium"

    def test_gate_is_skipped_when_no_python_changed(self) -> None:
        # A documentation-only pull request must not trip `tests=0`.
        report = Report(ignored_files=["README.md"])
        gate = evaluate(report, parse_conditions(["tests=0", "confidence=high"]))
        assert gate.skipped
        assert not gate.failed
        assert not any(r.tripped for r in gate.results)

    def test_understated_when_a_number_passes_on_a_degraded_report(self) -> None:
        gate = evaluate(build_report(direct=1, confidence=MEDIUM), parse_conditions(["affected>9"]))
        assert gate.understated

    def test_not_understated_on_a_high_confidence_report(self) -> None:
        gate = evaluate(build_report(direct=1), parse_conditions(["affected>9"]))
        assert not gate.understated

    def test_not_understated_when_only_a_confidence_condition_passed(self) -> None:
        # Nothing was under-counted: the gate asked about confidence itself.
        gate = evaluate(build_report(confidence=MEDIUM), parse_conditions(["confidence=low"]))
        assert not gate.understated

    def test_no_conditions_is_a_passing_gate(self) -> None:
        gate = evaluate(build_report(direct=9), [])
        assert not gate.failed
        assert gate.metrics["affected"] == 9


class TestRendering:
    def test_failed_gate_names_the_expression_and_the_actual(self) -> None:
        gate = evaluate(build_report(direct=5), parse_conditions(["affected>4"]))
        text = render_gate(gate)
        assert "Gate: failed" in text
        assert "FAIL  affected>4  (actual: affected = 5)" in text

    def test_passing_conditions_are_shown_too(self) -> None:
        gate = evaluate(build_report(direct=1, tests=2), parse_conditions(["tests=0"]))
        text = render_gate(gate)
        assert "Gate: passed" in text
        assert "ok" in text
        assert "tests = 2" in text

    def test_expressions_are_column_aligned(self) -> None:
        gate = evaluate(build_report(direct=1), parse_conditions(["affected>99", "tests=0"]))
        actual_columns = {line.index("(actual:") for line in render_gate(gate).splitlines()[1:]}
        assert len(actual_columns) == 1

    def test_understated_note_is_rendered(self) -> None:
        gate = evaluate(build_report(direct=1, confidence=LOW), parse_conditions(["affected>9"]))
        assert "confidence is Low" in render_gate(gate)

    def test_skipped_gate_says_so(self) -> None:
        gate = evaluate(Report(), parse_conditions(["tests=0"]))
        assert render_gate(gate) == "Gate: skipped (no Python changes to measure)"
