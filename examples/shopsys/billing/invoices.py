"""Invoice adjustments triggered by refund outcomes."""

from api.refunds import handle_refund_request


def credit_invoice_for_refund(invoice: dict, payload: dict) -> dict:
    status, body = handle_refund_request(payload)
    if status == 200:
        invoice = {**invoice, "credited_cents": body["amount_cents"]}
    return invoice
