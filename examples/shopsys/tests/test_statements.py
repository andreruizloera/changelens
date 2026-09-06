from billing.statements import render_statement_line


def test_amount_is_rendered_as_dollars():
    entry = {"date": "2026-01-31", "description": "refund", "amount_cents": 1250}
    assert render_statement_line(entry).endswith("$12.50")
