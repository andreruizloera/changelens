from __future__ import annotations

import json

from changelens.impact import HIGH, MEDIUM, ChangedSymbol, Dependent, Report
from changelens.report import render_json, render_mermaid, render_terminal


def sample_report() -> Report:
    return Report(
        changed=[ChangedSymbol("payments/refund.py", "refund_payment", "function")],
        direct_dependents=[
            Dependent(
                file="api/refunds.py",
                module="api.refunds",
                distance=1,
                calls_changed_symbol=True,
                called_symbols=("refund_payment",),
                via="payments/refund.py",
            ),
            Dependent(
                file="jobs/retry_refunds.py",
                module="jobs.retry_refunds",
                distance=1,
                via="payments/refund.py",
            ),
        ],
        potentially_affected=[
            Dependent(
                file="billing/invoices.py",
                module="billing.invoices",
                distance=2,
                via="api/refunds.py",
            )
        ],
        relevant_tests=[
            Dependent(
                file="tests/test_refunds.py",
                module="tests.test_refunds",
                distance=2,
                via="api/refunds.py",
            )
        ],
    )


def test_terminal_report_shape() -> None:
    out = render_terminal(sample_report())
    lines = out.splitlines()
    assert lines[0] == "Change blast radius"
    assert lines[1] == "-------------------"
    assert "Changed:" in lines
    assert "  payments/refund.py::refund_payment" in lines
    assert "Direct dependents:" in lines
    assert "  api/refunds.py  (calls refund_payment)" in lines
    assert "  jobs/retry_refunds.py" in lines
    assert "Potentially affected:" in lines
    assert "  billing/invoices.py  (via api/refunds.py)" in lines
    assert "Relevant tests:" in lines
    assert lines.index("Changed:") < lines.index("Direct dependents:")
    assert lines.index("Direct dependents:") < lines.index("Potentially affected:")
    assert lines.index("Potentially affected:") < lines.index("Relevant tests:")
    assert out.rstrip().endswith(
        "Confidence: High (all changed symbols resolved; import graph is complete)"
    )


def test_terminal_report_shows_confidence_reasons() -> None:
    report = sample_report()
    report.confidence = MEDIUM
    report.confidence_reasons = ["wild.py star-imports a changed module"]
    out = render_terminal(report)
    assert "Confidence: Medium" in out
    assert "  - wild.py star-imports a changed module" in out


def test_terminal_report_empty_sections() -> None:
    out = render_terminal(Report(changed=[ChangedSymbol("a.py", None, "module")]))
    assert "  a.py" in out
    assert out.count("  (none found)") == 3


def test_json_schema() -> None:
    payload = json.loads(render_json(sample_report()))
    assert payload["schema_version"] == 5
    assert set(payload) == {
        "schema_version",
        "changed",
        "direct_dependents",
        "potentially_affected",
        "relevant_tests",
        "confidence",
        "ignored_files",
        "metrics",
    }
    # "gate" is the one key that appears only when --fail-on was passed.
    assert payload["metrics"] == {
        "changed": 1,
        "direct": 2,
        "transitive": 1,
        "tests": 1,
        "affected": 4,
        "distance": 2,
    }
    assert payload["changed"] == [
        {"file": "payments/refund.py", "symbol": "refund_payment", "kind": "function"}
    ]
    dep = payload["direct_dependents"][0]
    assert set(dep) == {
        "file",
        "module",
        "distance",
        "calls_changed_symbol",
        "called_symbols",
        "via",
    }
    assert dep["calls_changed_symbol"] is True
    assert payload["confidence"] == {"level": "high", "reasons": []}


def test_json_confidence_level_lowercase() -> None:
    report = sample_report()
    report.confidence = MEDIUM
    report.confidence_reasons = ["reason"]
    payload = json.loads(render_json(report))
    assert payload["confidence"]["level"] == "medium"
    assert report.confidence != HIGH  # sanity


def test_mermaid_output() -> None:
    out = render_mermaid(sample_report())
    lines = out.splitlines()
    assert lines[0] == "flowchart TD"
    assert any("payments/refund.py::refund_payment" in ln and ":::changed" in ln for ln in lines)
    assert any(":::dependent" in ln for ln in lines)
    assert any(":::affected" in ln for ln in lines)
    assert any(":::test" in ln for ln in lines)
    # Direct dependent points at the changed node.
    assert any(
        ln.strip() == "d_api_refunds_py --> c_payments_refund_py__refund_payment" for ln in lines
    )
    # Transitive node points at its via node, tests use dashed edges.
    assert any(ln.strip() == "p_billing_invoices_py --> d_api_refunds_py" for ln in lines)
    assert any("-.->" in ln for ln in lines)
    assert "classDef changed" in out
