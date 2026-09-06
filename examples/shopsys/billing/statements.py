"""Customer-facing statement lines."""

from payments.money import format_cents


def render_statement_line(entry: dict) -> str:
    amount = format_cents(entry["amount_cents"])
    return f"{entry['date']}  {entry['description']}  {amount}"
