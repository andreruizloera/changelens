from __future__ import annotations

import pytest

from changelens.baseline import Baseline, Spec
from changelens.gate import (
    GateError,
    baseline_from,
    evaluate,
    file_sets_of,
    files_for,
    metrics_of,
    needs_baseline,
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


def baseline_of(**kwargs: object) -> Baseline:
    """A baseline taken from a report built the same way the others are."""
    return baseline_from(build_report(**kwargs), Spec(ref="main"))  # type: ignore[arg-type]


class TestBaselineParsing:
    @pytest.mark.parametrize(
        ("expression", "offset", "text"),
        [
            ("affected>baseline", 0, "baseline"),
            ("affected>baseline+10", 10, "baseline+10"),
            ("affected<baseline-3", -3, "baseline-3"),
            ("affected > baseline + 10", 10, "baseline+10"),
            ("AFFECTED>BASELINE+2", 2, "baseline+2"),
        ],
    )
    def test_relative_forms(self, expression: str, offset: int, text: str) -> None:
        condition = parse_condition(expression)
        assert condition.relative
        assert (condition.value, condition.value_text) == (offset, text)

    def test_a_constant_condition_is_not_relative(self) -> None:
        assert not parse_condition("affected>20").relative

    def test_confidence_compares_against_the_baseline_itself(self) -> None:
        condition = parse_condition("confidence>baseline")
        assert (condition.relative, condition.metric) == (True, "confidence")

    def test_confidence_rejects_an_offset(self) -> None:
        # A step on a three-level risk scale is not a quantity.
        with pytest.raises(GateError, match="not an offset"):
            parse_condition("confidence>baseline+1")

    def test_unknown_metric_is_caught_before_the_baseline_word(self) -> None:
        with pytest.raises(GateError, match="unknown metric 'blastradius'"):
            parse_condition("blastradius>baseline")

    def test_a_word_that_is_not_baseline_is_still_rejected(self) -> None:
        with pytest.raises(GateError, match="whole number or a baseline"):
            parse_condition("affected>basline")

    def test_needs_baseline_only_when_something_relative_is_present(self) -> None:
        assert not needs_baseline(parse_conditions(["affected>20", "confidence=low"]))
        assert needs_baseline(parse_conditions(["affected>20", "tests<baseline"]))


class TestFileSets:
    def test_affected_is_the_union_of_the_three_buckets(self) -> None:
        sets = file_sets_of(build_report(direct=2, transitive=1, tests=1))
        assert files_for(sets, "affected") == ("app0.py", "app1.py", "far0.py", "tests/test_0.py")

    def test_a_bucket_returns_only_its_own_files(self) -> None:
        sets = file_sets_of(build_report(direct=2, tests=1))
        assert files_for(sets, "direct") == ("app0.py", "app1.py")

    def test_metrics_that_count_no_files_say_so(self) -> None:
        sets = file_sets_of(build_report(direct=1))
        assert files_for(sets, "distance") is None
        assert files_for(sets, "confidence") is None

    def test_changed_files_are_deduplicated(self) -> None:
        report = Report()
        report.changed.append(ChangedSymbol("pkg/a.py", "one", "function"))
        report.changed.append(ChangedSymbol("pkg/a.py", "two", "function"))
        assert file_sets_of(report)["changed"] == ("pkg/a.py",)

    def test_a_saved_baseline_carries_the_spec_and_the_counts(self) -> None:
        baseline = baseline_from(build_report(direct=2), Spec(ref="main"), head="abc", version="9")
        assert baseline.spec == Spec(ref="main")
        assert baseline.metrics["affected"] == 2
        assert baseline.files["direct"] == ("app0.py", "app1.py")
        assert (baseline.head, baseline.version) == ("abc", "9")


class TestBaselineEvaluation:
    def test_growth_trips_and_a_flat_run_does_not(self) -> None:
        conditions = parse_conditions(["affected>baseline"])
        baseline = baseline_of(direct=2)
        assert evaluate(build_report(direct=3), conditions, baseline).failed
        assert not evaluate(build_report(direct=2), conditions, baseline).failed

    def test_an_offset_is_room_to_grow(self) -> None:
        conditions = parse_conditions(["affected>baseline+2"])
        baseline = baseline_of(direct=2)
        assert not evaluate(build_report(direct=4), conditions, baseline).failed
        assert evaluate(build_report(direct=5), conditions, baseline).failed

    def test_a_negative_offset_gates_on_shrinking(self) -> None:
        # "fail if this branch dropped more than one file out of the radius"
        conditions = parse_conditions(["affected<baseline-1"])
        baseline = baseline_of(direct=5)
        assert not evaluate(build_report(direct=4), conditions, baseline).failed
        assert evaluate(build_report(direct=3), conditions, baseline).failed

    def test_the_result_carries_the_baseline_and_the_delta(self) -> None:
        gate = evaluate(
            build_report(direct=5), parse_conditions(["affected>baseline"]), baseline_of(direct=2)
        )
        result = gate.results[0]
        assert (result.actual, result.baseline, result.delta) == (5, 2, 3)

    def test_a_constant_condition_has_no_delta(self) -> None:
        gate = evaluate(build_report(direct=5), parse_conditions(["affected>1"]), baseline_of())
        assert gate.results[0].delta is None

    def test_entrants_are_the_files_the_radius_gained(self) -> None:
        gate = evaluate(
            build_report(direct=3), parse_conditions(["affected>baseline"]), baseline_of(direct=1)
        )
        assert gate.results[0].entered == ("app1.py", "app2.py")

    def test_entrants_are_scoped_to_the_metric(self) -> None:
        gate = evaluate(
            build_report(direct=2, tests=2),
            parse_conditions(["tests>baseline"]),
            baseline_of(direct=1, tests=1),
        )
        assert gate.results[0].entered == ("tests/test_1.py",)

    def test_a_file_that_left_the_radius_is_not_an_entrant(self) -> None:
        gate = evaluate(
            build_report(direct=1), parse_conditions(["affected!=baseline"]), baseline_of(direct=3)
        )
        assert gate.results[0].entered == ()

    def test_distance_has_no_entrants(self) -> None:
        gate = evaluate(
            build_report(direct=1, transitive=1, max_distance=5),
            parse_conditions(["distance>baseline"]),
            baseline_of(direct=1, transitive=1, max_distance=2),
        )
        assert gate.results[0].tripped
        assert gate.results[0].entered == ()

    def test_a_baseline_without_file_lists_still_gates(self) -> None:
        counted_only = Baseline(metrics={"affected": 1}, confidence=HIGH, spec=Spec(ref="main"))
        gate = evaluate(
            build_report(direct=4), parse_conditions(["affected>baseline"]), counted_only
        )
        assert gate.results[0].tripped
        assert gate.results[0].entered == ()

    def test_confidence_trips_when_this_run_is_less_trustworthy(self) -> None:
        conditions = parse_conditions(["confidence>baseline"])
        high = baseline_of(confidence=HIGH)
        assert evaluate(build_report(confidence=MEDIUM), conditions, high).failed
        assert not evaluate(build_report(confidence=HIGH), conditions, high).failed
        low = baseline_of(confidence=LOW)
        assert not evaluate(build_report(confidence=MEDIUM), conditions, low).failed

    def test_a_relative_condition_without_a_baseline_is_an_error_not_a_pass(self) -> None:
        with pytest.raises(GateError, match="none was loaded"):
            evaluate(build_report(direct=9), parse_conditions(["affected>baseline"]))

    def test_a_baseline_missing_the_metric_is_an_error(self) -> None:
        older = Baseline(metrics={"affected": 1}, confidence=HIGH, spec=Spec(ref="main"))
        with pytest.raises(GateError, match="no 'tests' metric"):
            evaluate(build_report(tests=2), parse_conditions(["tests>baseline"]), older)

    def test_a_baseline_with_an_unreadable_confidence_is_an_error(self) -> None:
        broken = Baseline(metrics={"affected": 1}, confidence="Probably", spec=Spec(ref="main"))
        with pytest.raises(GateError, match="not one of"):
            evaluate(build_report(), parse_conditions(["confidence>baseline"]), broken)

    def test_a_docs_only_diff_skips_relative_conditions_too(self) -> None:
        gate = evaluate(
            Report(ignored_files=["README.md"]),
            parse_conditions(["affected>baseline"]),
            baseline_of(direct=5),
        )
        assert gate.skipped and not gate.failed

    def test_confidence_drift_is_reported_when_the_levels_differ(self) -> None:
        gate = evaluate(
            build_report(direct=1, confidence=MEDIUM),
            parse_conditions(["affected>baseline"]),
            baseline_of(direct=1, confidence=HIGH),
        )
        assert gate.confidence_drift == HIGH

    def test_no_drift_when_both_read_at_the_same_level(self) -> None:
        gate = evaluate(
            build_report(direct=1), parse_conditions(["affected>baseline"]), baseline_of(direct=1)
        )
        assert gate.confidence_drift is None

    def test_no_drift_note_for_a_constant_only_gate(self) -> None:
        # The baseline was loaded for another condition; this one does not use it.
        gate = evaluate(
            build_report(direct=1, confidence=LOW),
            parse_conditions(["affected>99"]),
            baseline_of(direct=1, confidence=HIGH),
        )
        assert gate.confidence_drift is None


class TestBaselineRendering:
    def test_the_line_shows_the_baseline_and_the_movement(self) -> None:
        gate = evaluate(
            build_report(direct=9), parse_conditions(["affected>baseline"]), baseline_of(direct=6)
        )
        assert "FAIL  affected>baseline  (actual: affected = 9, baseline 6, +3)" in render_gate(
            gate
        )

    def test_a_flat_run_reads_as_no_change(self) -> None:
        gate = evaluate(
            build_report(direct=6), parse_conditions(["affected>=baseline"]), baseline_of(direct=6)
        )
        assert "baseline 6, no change" in render_gate(gate)

    def test_a_shrink_is_signed(self) -> None:
        gate = evaluate(
            build_report(direct=4), parse_conditions(["affected<baseline"]), baseline_of(direct=6)
        )
        assert "baseline 6, -2" in render_gate(gate)

    def test_entrants_are_listed_under_a_tripped_condition(self) -> None:
        gate = evaluate(
            build_report(direct=3), parse_conditions(["affected>baseline"]), baseline_of(direct=1)
        )
        text = render_gate(gate)
        assert "2 files entered the radius since the baseline:" in text
        assert "          app1.py" in text
        assert "          app2.py" in text

    def test_one_entrant_is_singular(self) -> None:
        gate = evaluate(
            build_report(direct=2), parse_conditions(["affected>baseline"]), baseline_of(direct=1)
        )
        assert "1 file entered the radius" in render_gate(gate)

    def test_the_phrase_follows_the_metric(self) -> None:
        gate = evaluate(
            build_report(tests=2), parse_conditions(["tests>baseline"]), baseline_of(tests=1)
        )
        assert "became relevant tests since the baseline" in render_gate(gate)

    def test_a_long_list_is_capped(self) -> None:
        gate = evaluate(
            build_report(direct=9), parse_conditions(["affected>baseline"]), baseline_of(direct=1)
        )
        text = render_gate(gate)
        assert "8 files entered the radius since the baseline:" in text
        assert "... and 3 more" in text
        assert "app6.py" not in text

    def test_a_passing_condition_does_not_list_files(self) -> None:
        gate = evaluate(
            build_report(direct=3), parse_conditions(["affected>baseline+5"]), baseline_of(direct=1)
        )
        assert "entered the radius" not in render_gate(gate)

    def test_confidence_movement_is_a_word_not_a_number(self) -> None:
        gate = evaluate(
            build_report(confidence=LOW),
            parse_conditions(["confidence>baseline"]),
            baseline_of(confidence=HIGH),
        )
        assert "(actual: confidence = low, baseline high, worse)" in render_gate(gate)

    def test_the_drift_note_is_rendered(self) -> None:
        gate = evaluate(
            build_report(direct=1, confidence=MEDIUM),
            parse_conditions(["affected>baseline"]),
            baseline_of(direct=1, confidence=HIGH),
        )
        text = render_gate(gate)
        assert "the baseline read at High confidence and this run reads at Medium" in text
        assert "edges becoming visible or going" in text
