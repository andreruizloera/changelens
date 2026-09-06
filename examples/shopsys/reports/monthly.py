"""Monthly statement, assembled from statement lines."""

from billing.statements import render_statement_line


def monthly_statement(entries: list[dict]) -> str:
    return "\n".join(render_statement_line(entry) for entry in entries)
